from sdfb_core.seeding import derive_batch_seed


def test_deterministic_per_run_and_batch():
    assert derive_batch_seed("run-a", 0) == derive_batch_seed("run-a", 0)


def test_batches_differ():
    seeds = {derive_batch_seed("run-a", i) for i in range(100)}
    assert len(seeds) == 100


def test_runs_differ():
    assert derive_batch_seed("run-a", 0) != derive_batch_seed("run-b", 0)


def test_fits_in_signed_64bit_and_nonnegative():
    s = derive_batch_seed("x" * 500, 10**9)
    assert 0 <= s < 2**63
