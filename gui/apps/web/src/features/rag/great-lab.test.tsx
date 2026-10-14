/**
 * The GReaT + embedder lab computes every step with the exact ports: the
 * parsed row re-serialises to the stored chunk_text byte for byte, the
 * re-embedded vector matches the stored one, tokens map to their buckets,
 * and an edit flows through the sentence, the tokens and the heatmap.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RagChunksQuery } from "@contracts/api";
import { hashingBucket, hashingTokens } from "@synthetic-platform/stats";

import { GreatLab } from "./lab/GreatLab";
import { fixtureModel, rowDocEnvelope, rowDocTexts } from "./testFixtures";

const envelope = rowDocEnvelope();

vi.mock("@/lib/api", () => ({
  useRagChunks: (query: RagChunksQuery | undefined) =>
    query?.embedder.startsWith("hashing-384")
      ? { data: { data: envelope }, isPending: false, isFetching: false }
      : { data: undefined, isPending: false, isFetching: false },
}));

// ECharts is not under test here; the frame's title and table twin are.
vi.mock("@/components/ChartFrame", () => ({
  ChartFrame: ({ title, data }: { title: string; data: unknown[] }) => (
    <figure aria-label={title}>
      <figcaption>{title}</figcaption>
      <span>{data.length} bins</span>
    </figure>
  ),
}));

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

describe("GReaT + embedder lab", { timeout: 60_000 }, () => {
  it("rebuilds the stored sentence and vector exactly", () => {
    render(<GreatLab model={fixtureModel()} />);
    const text = rowDocTexts()[0]!;
    expect(screen.getByTestId("great-sentence")).toHaveTextContent(text);
    expect(screen.getByText("identical to the stored chunk_text")).toBeInTheDocument();
    expect(screen.getByText(/matches the stored vector/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "city" })).toHaveValue("Port Pine");
  });

  it("lists the str.split() tokens with the bucket and sign of the exact port", () => {
    render(<GreatLab model={fixtureModel()} />);
    const text = rowDocTexts()[0]!;
    const tokens = hashingTokens(text);
    const list = screen.getByRole("list", { name: "Tokens and their buckets" });
    const buttons = within(list).getAllByRole("button");
    expect(buttons).toHaveLength(tokens.length);
    tokens.forEach((token, i) => {
      const { bucket, sign } = hashingBucket(token, 384, 0);
      expect(buttons[i]).toHaveAccessibleName(`Token ${token}: bucket ${bucket}, sign ${sign > 0 ? "plus" : "minus"}`);
    });
    expect(
      screen.getByRole("img", { name: /The hashing-384 vector of this row: \d+ of 384 dimensions non-zero/ }),
    ).toBeInTheDocument();
  });

  it("follows an edit through the sentence and the tokens, and can undo it", async () => {
    const user = userEvent.setup();
    render(<GreatLab model={fixtureModel()} />);
    const city = screen.getByRole("textbox", { name: "city" });
    await user.clear(city);
    await user.type(city, "Gotham");

    expect(screen.getByTestId("great-sentence")).toHaveTextContent("city is Gotham, country is Spain");
    expect(screen.getByText(/edited — differs from the stored text/)).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Tokens and their buckets" });
    const { bucket } = hashingBucket("Gotham,", 384, 0);
    expect(
      within(list).getByRole("button", { name: new RegExp(`^Token Gotham,: bucket ${bucket},`) }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Undo my edits/ }));
    expect(screen.getByText("identical to the stored chunk_text")).toBeInTheDocument();
  });

  it("shows the bge side honestly when the sample has no bge vectors, with the pooling note", () => {
    render(<GreatLab model={fixtureModel()} />);
    expect(screen.getByText(/This sample has no bge vectors for this row/)).toBeInTheDocument();
    const note = screen.getByRole("note", { name: "" });
    expect(note).toHaveTextContent(/mean-pooled, not CLS-pooled/);
    expect(within(note).getByRole("link", { name: /embedding\.py/ })).toHaveAttribute(
      "href",
      expect.stringContaining("embedding.py"),
    );
  });

  it("switches rows and keeps the cosine charts' data", async () => {
    const user = userEvent.setup();
    render(<GreatLab model={fixtureModel()} />);
    const row = screen.getByRole("spinbutton", { name: /Row document/ });
    await user.clear(row);
    await user.type(row, "3");
    expect(screen.getByTestId("great-sentence")).toHaveTextContent(rowDocTexts()[2]!);
    expect(screen.getByRole("figure", { name: /Cosine to the other rows \(hashing-384\)/ })).toHaveTextContent(/bins/);
  });
});
