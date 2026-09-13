"""Convert the CMS Open Payments raw CSVs to Parquet once.

Measured: 29 GB of CSV becomes 718 MB of ZSTD Parquet -- about 40x. Queries against the
Parquet run in 0.1-0.6s, which matches what the same queries cost on a hosted columnar
warehouse, without any upload step or compute billing.

Download the detail files first from https://openpaymentsdata.cms.gov/datasets and point
SRC at the directory holding them.
"""
import duckdb, os, sys, time, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SRC, OUT = "data/raw", "data/parquet"
os.makedirs(OUT, exist_ok=True)
con = duckdb.connect()
con.execute("SET memory_limit='6GB'; SET threads=4; SET preserve_insertion_order=false;")

for tag, prefix in (("op_general", "OP_DTL_GNRL_"), ("op_research", "OP_DTL_RSRCH_")):
    dest = f"{OUT}/{tag}.parquet"
    if os.path.exists(dest) and tag == "op_research":
        print(f"{tag}: already built ({os.path.getsize(dest)/1e6:.0f} MB)"); continue
    srcs = [f for f in os.listdir(SRC) if f.startswith(prefix) and f.endswith(".csv")]
    raw = sum(os.path.getsize(os.path.join(SRC, f)) for f in srcs)
    print(f"{tag}: {len(srcs)} files, {raw/1e9:.2f} GB -> Parquet ...", flush=True)
    t0 = time.time()
    con.execute(f"""COPY (SELECT * FROM read_csv('{SRC}/{prefix}*.csv',
      union_by_name=true, ignore_errors=true,
      types={{'Total_Amount_of_Payment_USDollars':'DOUBLE'}}))
      TO '{dest}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
    el, sz = time.time() - t0, os.path.getsize(dest)
    print(f"  {el/60:.1f} min  ->  {sz/1e6:.0f} MB  ({raw/sz:.1f}x smaller)", flush=True)
    n = con.execute(f"SELECT count(*) FROM '{dest}'").fetchone()[0]
    print(f"  {n:,} rows", flush=True)
