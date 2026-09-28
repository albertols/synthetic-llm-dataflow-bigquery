/** Hand values and properties for the non-golden maths. */
import { describe, expect, it } from "vitest";

import { catalogueById } from "@contracts/generated/catalogue";

import { contingencyTvd, cramersV, nmi, pearson, spearman } from "./correlation";
import { cohensW, hellinger, jsdBits, psi, tvd } from "./distances";
import {
  decileKsLegacy,
  ecdf,
  histogram,
  ksBracket,
  pitW1,
  profilerDeciles,
  pyRound,
  quantiles,
  quantilesFromBins,
  w1FromBins,
} from "./distributions";
import { entropyBits, normalizedEntropy } from "./entropy";
import {
  aucInterval,
  binormalRoc,
  dkwEpsilon,
  jsdNullExpectationBits,
  ksCritical,
  miBiasNats,
  newcombe,
  noiseFloor,
  rateRatio,
  tvdNullExpectation,
  wilson,
} from "./intervals";
import { cosine, knnPreservation, pca3 } from "./linalg";
import { Random } from "./rng";
import { birthdayCollisionProb, expectedDistinct, poolReuse, rarefaction, rareCaptureProb, tailPoints } from "./scale";
import { modelFamilyScores, scoreValue, statusFor, tableFamilyScores } from "./scoring";
import { betaQuantile, incompleteBeta, normalCdf, normalQuantile } from "./special";
import { mmr, randomPick, TEACHING_ONLY } from "./teaching";

describe("noise floors and intervals", () => {
  it("DKW at n = 10,000 is 0.01358", () => {
    expect(dkwEpsilon(10_000)).toBeCloseTo(0.01358, 5);
    expect(dkwEpsilon(1_000_000)).toBeCloseTo(0.001358, 6);
  });

  it("the two-sample KS critical value uses c(0.05) = 1.358", () => {
    expect(ksCritical(10_000, 10_000)).toBeCloseTo(1.3581 * Math.sqrt(2 / 10_000), 5);
  });

  it("Wilson matches the textbook interval", () => {
    const [lo, hi] = wilson(81, 263);
    expect(lo).toBeCloseTo(0.2553, 3);
    expect(hi).toBeCloseTo(0.3662, 3);
    expect(wilson(0, 0)).toEqual([0, 1]);
    expect(wilson(0, 1000)[1]).toBeCloseTo(0.00383, 4);
  });

  it("Newcombe matches Newcombe 1998's worked example (56/70 − 48/80)", () => {
    const [lo, hi] = newcombe(56, 70, 48, 80);
    expect(lo).toBeCloseTo(0.0524, 3);
    expect(hi).toBeCloseTo(0.3339, 3);
  });

  it("the TVD and JSD null expectations shrink with n", () => {
    expect(tvdNullExpectation([0.5, 0.5], 1000, 1000)).toBeCloseTo(
      0.5 * 2 * Math.sqrt((2 * 0.25 * 0.002) / Math.PI),
      10,
    );
    expect(jsdNullExpectationBits(5, 10_000, 10_000)).toBeCloseTo((4 * 0.0002) / (8 * Math.LN2), 12);
    expect(miBiasNats(5, 5, 10_000)).toBe(16 / 20_000);
  });

  it("the rate ratio behaves like the evaluator's tests", () => {
    const even = rateRatio(20, 1000, 20, 1000);
    expect(even.lo).toBeLessThan(1);
    expect(even.hi).toBeGreaterThan(1);
    const leak = rateRatio(30, 1e4, 3, 1e4);
    expect(leak.ratio).toBeCloseTo(10, 10);
    expect(leak.lo).toBeGreaterThan(2.5);
    expect(rateRatio(0, 10, 0, 10)).toEqual({ ratio: null, lo: 0, hi: Infinity });
    expect(rateRatio(8, 100, 0, 100).ratio).toBeNull();
    expect(rateRatio(8, 100, 0, 100, 0.05, { zeroCorrection: true }).ratio).toBeCloseTo(17, 12);
  });

  it("special functions invert each other", () => {
    for (const p of [0.001, 0.025, 0.5, 0.975, 0.999]) expect(normalCdf(normalQuantile(p))).toBeCloseTo(p, 6);
    expect(normalQuantile(0.975)).toBeCloseTo(1.959964, 5);
    expect(incompleteBeta(betaQuantile(0.3, 4, 7), 4, 7)).toBeCloseTo(0.3, 9);
  });

  it("the AUC interval and the binormal ROC are coherent", () => {
    const [lo, hi] = aucInterval(0.75, 500, 500);
    expect(lo).toBeLessThan(0.75);
    expect(hi).toBeGreaterThan(0.75);
    const roc = binormalRoc(0.75, 101);
    const area = roc.slice(1).reduce((acc, [x, y], i) => acc + ((x - roc[i]![0]) * (y + roc[i]![1])) / 2, 0);
    expect(area).toBeCloseTo(0.75, 2);
  });

  it("dispatches noise floors by the catalogue's method name", () => {
    expect(noiseFloor("ks_two_sample", { n: 10_000, m: 10_000 })).toBeCloseTo(ksCritical(10_000, 10_000), 12);
    expect(noiseFloor("rate_ratio", { n: 10, m: 10 })).toBeNull();
    expect(noiseFloor("none", {})).toBeNull();
  });
});

describe("distances and entropy", () => {
  it("TVD and JSD on hand values", () => {
    expect(tvd([0.5, 0.5], [0.9, 0.1])).toBeCloseTo(0.4, 12);
    expect(tvd([10, 10], [5, 5])).toBe(0);
    expect(tvd([1, 0], [0, 1])).toBe(1);
    expect(jsdBits([1, 0], [0, 1])).toBeCloseTo(1, 12);
    expect(jsdBits([0.5, 0.5], [0.5, 0.5])).toBe(0);
    // JSD([.5,.5],[.9,.1]) = H(.7,.3) − (H(.5,.5) + H(.9,.1))/2
    const h = (a: number, b: number) => -(a * Math.log2(a) + b * Math.log2(b));
    expect(jsdBits([0.5, 0.5], [0.9, 0.1])).toBeCloseTo(h(0.7, 0.3) - (1 + h(0.9, 0.1)) / 2, 12);
    expect(tvd([0, 0], [1, 1])).toBeNull();
  });

  it("PSI, Cohen's w and Hellinger", () => {
    expect(psi([100, 100], [100, 100])).toBeCloseTo(0, 12);
    expect(psi([1000, 0], [0, 1000])!).toBeGreaterThan(5);
    expect(cohensW([0.5, 0.5], [0.5, 0.5])).toBe(0);
    expect(hellinger([1, 0], [0, 1])).toBeCloseTo(1, 12);
  });

  it("entropy in bits, normalized by log2(distinct)", () => {
    expect(entropyBits([1, 1, 1, 1])).toBeCloseTo(2, 12);
    expect(normalizedEntropy([5, 5])).toBeCloseTo(1, 12);
    expect(normalizedEntropy([7])).toBe(0);
    expect(entropyBits([])).toBeNull();
  });
});

describe("binned CDF maths", () => {
  const rng = new Random(11);
  const source = Array.from({ length: 20_000 }, () => rng.normal());
  const same = Array.from({ length: 20_000 }, () => rng.normal());
  const shifted = source.map((v) => v + 500);
  const edges = quantiles(
    source,
    Array.from({ length: 99 }, (_, i) => (i + 1) / 100),
  );

  it("bins follow searchsorted(side=left): x ≤ e0 falls in bin 0", () => {
    expect(histogram([0, 1, 1.5, 2, 3], [1, 2])).toEqual([2, 2, 1]);
    expect(histogram([Number.NaN], [1])).toEqual([0, 0]);
  });

  it("the KS bracket contains the exact KS distance and resolves a +500 shift", () => {
    const cs = histogram(source, edges);
    const cy = histogram(same, edges);
    const bracket = ksBracket(cs, cy)!;
    expect(bracket.dLo).toBeLessThanOrEqual(bracket.dHi);
    expect(bracket.dHi - bracket.dLo).toBeLessThan(0.03);
    expect(bracket.dHi).toBeLessThan(0.05);
    const far = ksBracket(cs, histogram(shifted, edges))!;
    expect(far.dLo).toBeGreaterThan(0.98);
  });

  it("PIT-W1 is ≈ 0 for one distribution, ≈ ½ for disjoint ones, never above ½", () => {
    const cs = histogram(source, edges);
    expect(pitW1(cs, histogram(same, edges))!).toBeLessThan(0.01);
    const far = pitW1(cs, histogram(shifted, edges))!;
    expect(far).toBeGreaterThan(0.49);
    expect(far).toBeLessThanOrEqual(0.5);
    expect(pitW1([1, 0], [0, 1])).toBeCloseTo(0.5, 12);
  });

  it("the legacy decile KS matches numpy.interp on the union grid", () => {
    const ramp = Array.from({ length: 11 }, (_, i) => i);
    // Values printed by the Python original (numpy 2.3).
    expect(decileKsLegacy(ramp, ramp)).toBe(0);
    expect(decileKsLegacy(new Array<number>(11).fill(1), ramp)).toBeCloseTo(0.9, 12);
    expect(decileKsLegacy([0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8], ramp)).toBeCloseTo(0.2, 12);
    expect(decileKsLegacy([0, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8], [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])).toBeCloseTo(
      0.4,
      12,
    );
  });

  it("W1 from bins, quantiles, ECDF and the profiler's deciles", () => {
    expect(w1FromBins([0, 1, 2], [0, 1, 0, 0], [0, 0, 1, 0])).toBeCloseTo(1, 12);
    expect(quantiles([1, 2, 3, 4], [0, 0.5, 1])).toEqual([1, 2.5, 4]);
    expect(ecdf([3, 1, 2]).at(2)).toBeCloseTo(2 / 3, 12);
    expect(pyRound(0.5)).toBe(0);
    expect(pyRound(1.5)).toBe(2);
    expect(pyRound(2.5)).toBe(2);
    expect(profilerDeciles([1, 2, 3, 4, 5, 6])).toEqual([1, 1, 2, 3, 3, 3, 4, 5, 5, 5, 6]); // Python: [o[round(i*5/10)] for i in range(11)]
    const qs = quantilesFromBins([10, 20], [0, 10, 0], [0.5], { min: 0, max: 30 });
    expect(qs[0]).toBeCloseTo(15, 12);
  });
});

describe("dependence", () => {
  it("Pearson, Spearman, Cramér's V and NMI on hand cases", () => {
    expect(pearson([1, 2, 3, 4], [2, 4, 6, 8])).toBeCloseTo(1, 12);
    expect(spearman([1, 2, 3, 4], [1, 4, 9, 16])).toBeCloseTo(1, 12);
    expect(pearson([1, 1, 1], [1, 2, 3])).toBeNull();
    expect(
      cramersV([
        [500, 0],
        [0, 500],
      ])!,
    ).toBeCloseTo(1, 2);
    expect(
      cramersV([
        [250, 250],
        [250, 250],
      ]),
    ).toBe(0);
    expect(
      nmi([
        [50, 0],
        [0, 50],
      ]),
    ).toBeCloseTo(1, 12);
    expect(contingencyTvd([[1, 0]], [[0, 1]])).toBe(1);
  });
});

describe("linear algebra", () => {
  it("PCA recovers a dominant axis and preserves neighbourhoods of a 3-D cloud", () => {
    const rng = new Random(3);
    const n = 300;
    const dim = 16;
    const data = new Float32Array(n * dim);
    for (let i = 0; i < n; i += 1) {
      const [a, b, c] = [rng.normal(0, 10), rng.normal(0, 5), rng.normal(0, 2)];
      for (let j = 0; j < dim; j += 1)
        data[i * dim + j] = (j === 0 ? a : j === 1 ? b : j === 2 ? c : 0) + rng.normal(0, 0.01);
    }
    const result = pca3(data, dim);
    expect(Math.abs(result.components[0]![0]!)).toBeGreaterThan(0.99);
    expect(result.explained[0]!).toBeGreaterThan(result.explained[1]!);
    expect(knnPreservation(data, dim, result.projected, 3, 8)).toBeGreaterThan(0.9);
    expect(cosine([1, 0], [0, 1])).toBe(0);
  });
});

describe("scale", () => {
  it("rare capture, tail points, collisions, pool reuse, rarefaction", () => {
    expect(rareCaptureProb(0.001, 10_000)).toBeCloseTo(1 - 0.999 ** 10_000, 12);
    expect(tailPoints(10_000, 0.999)).toBeCloseTo(10, 9);
    expect(birthdayCollisionProb(23, 365)).toBeCloseTo(0.5, 1);
    expect(poolReuse(90_000_000, 512)).toBeCloseTo(175_781.25, 6);
    expect(rarefaction([5, 5], 10)).toBe(2);
    expect(rarefaction([1, 1, 1, 1], 2)).toBeCloseTo(2, 9);
    expect(expectedDistinct([0.5, 0.5], 1)).toBeCloseTo(1, 12);
    expect(expectedDistinct([0.5, 0.5], 1000)).toBeCloseTo(2, 12);
  });
});

describe("teaching-only strategies", () => {
  it("are labelled and deterministic", () => {
    expect(TEACHING_ONLY).toEqual(["mmr", "randomPick"]);
    expect(randomPick(10, 3, 1)).toEqual(randomPick(10, 3, 1));
    expect(new Set(randomPick(10, 10, 1)).size).toBe(10);
    expect(
      mmr(
        [
          [1, 0],
          [0.9, 0.1],
          [0, 1],
        ],
        2,
        0.3,
      ),
    ).toHaveLength(2);
  });
});

describe("scoring mirrors the catalogue rules", () => {
  const ks = catalogueById["column.ks"];
  const lift = catalogueById["row.memorization_lift"];
  const pk = catalogueById["table.pk_duplicate_rate"];
  const adherence = catalogueById["field.category_adherence"];
  const novelty = catalogueById["column.novelty_mass"];

  it("scores with each function", () => {
    expect(scoreValue(ks, { value: 0.05 })).toBeCloseTo(0.95, 12);
    expect(scoreValue(adherence, { value: 0.97 })).toBeCloseTo(0.5, 12);
    expect(scoreValue(catalogueById["table.detection_auc"], { value: 0.75 })).toBeCloseTo(0.5, 12);
    expect(scoreValue(catalogueById["column.std_ratio"], { value: 1.05 })).toBe(1);
    expect(scoreValue(lift, { value: 8, ciLow: 3.5 })).toBeCloseTo(0.5, 12);
  });

  it("applies the D5 status rule: past fail but inside the noise floor is PASS", () => {
    expect(statusFor(ks, { value: 0.25, noiseFloor: 0.3 })).toBe("pass");
    expect(statusFor(ks, { value: 0.25, noiseFloor: 0.02 })).toBe("fail");
    expect(statusFor(ks, { value: 0.1, noiseFloor: 0.02 })).toBe("warn");
    expect(statusFor(ks, { value: null })).toBe("not_evaluated");
    expect(statusFor(catalogueById["column.wasserstein"], { value: 3 })).toBe("info");
    expect(statusFor(lift, { value: 8, ciLow: 1.5 })).toBe("pass");
    expect(statusFor(lift, { value: 8, ciLow: 5.5 })).toBe("fail");
    expect(statusFor(pk, { value: 1e-9 })).toBe("fail");
    expect(statusFor(pk, { value: 0 })).toBe("pass");
    expect(statusFor(adherence, { value: 0.95 })).toBe("fail");
    expect(statusFor(novelty, { value: 0.5, sourceValue: 0.1 })).toBe("fail");
  });

  it("rolls up per unit, then per family", () => {
    const scores = tableFamilyScores([
      {
        metric_id: "column.ks",
        table_name: "t",
        family: "fidelity",
        level: "column",
        column_name: "a",
        edge: null,
        score: 1,
      },
      {
        metric_id: "column.tvd",
        table_name: "t",
        family: "fidelity",
        level: "column",
        column_name: "a",
        edge: null,
        score: 0,
      },
      {
        metric_id: "column.ks",
        table_name: "t",
        family: "fidelity",
        level: "column",
        column_name: "b",
        edge: null,
        score: 1,
      },
      {
        metric_id: "table.fidelity_score",
        table_name: "t",
        family: "fidelity",
        level: "table",
        column_name: null,
        edge: null,
        score: 0,
      },
      {
        metric_id: "row.exact_match_rate",
        table_name: "t",
        family: "privacy",
        level: "row",
        column_name: null,
        edge: null,
        score: 0.5,
      },
    ]);
    expect(scores.fidelity).toBeCloseTo(0.75, 12);
    expect(scores.privacy).toBe(0.5);
    expect(scores.integrity).toBeNull();
    expect(scores.overall).toBeCloseTo(0.625, 12);
    expect(modelFamilyScores([scores, { ...scores, fidelity: 0.25 }]).fidelity).toBeCloseTo(0.5, 12);
  });
});
