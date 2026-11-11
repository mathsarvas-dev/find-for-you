"""Benchmark local bge-m3: 200 texts of ~300 words."""
import os
import platform
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rumo_core.embeddings.bge_m3 import BgeM3Embedder  # noqa: E402

N_TEXTS = 200
WORDS = 300


def _win_mem() -> tuple[float, float]:
    """(working set, peak working set) in MB via psapi, no extra dependency."""
    import ctypes
    from ctypes import wintypes

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ps = ctypes.WinDLL("psapi", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    if not ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return pmc.WorkingSetSize / 1e6, pmc.PeakWorkingSetSize / 1e6


def memory_mb() -> tuple[float, float]:
    """(current RSS, peak RSS) in MB."""
    try:
        import psutil

        cur = psutil.Process().memory_info().rss / 1e6
        if sys.platform == "win32":
            return cur, _win_mem()[1]
        import resource

        return cur, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except ImportError:
        pass
    if sys.platform == "win32":
        return _win_mem()
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return peak, peak


def main() -> None:
    import torch

    rng = random.Random(42)
    vocab = [f"palavra{i}" for i in range(2000)]
    texts = [" ".join(rng.choices(vocab, k=WORDS)) for _ in range(N_TEXTS)]

    emb = BgeM3Embedder()
    t0 = time.perf_counter()
    emb.embed(["warmup"])
    load_s = time.perf_counter() - t0
    device = emb._model.device

    t0 = time.perf_counter()
    out = emb.embed(texts)
    dt = time.perf_counter() - t0
    assert len(out) == N_TEXTS

    print(f"machine: {platform.processor()} | cpus={os.cpu_count()} | cuda={torch.cuda.is_available()}")
    print(f"torch {torch.__version__} device={device}")
    print(f"model load+first call: {load_s:.1f}s")
    print(f"{N_TEXTS} texts x ~{WORDS} words in {dt:.1f}s -> {N_TEXTS / dt:.2f} texts/s")
    cur, peak = memory_mb()
    print(f"memory: current RSS {cur:.0f} MB, peak {peak:.0f} MB")


if __name__ == "__main__":
    main()
