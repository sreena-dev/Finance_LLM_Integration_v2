import json
import os
from pathlib import Path

import polars as pl

from modes.trial_balance.pipeline.tools._shared import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.canonical_schema import *  # noqa: F401,F403
from modes.trial_balance.pipeline.tools.pipeline_tool import *  # noqa: F401,F403

# NOTE: process_input_documents (the old worksheet-classifier + manual/agent-supplied
# column-mapping ingestion tool) was retired -- ingest_tb_to_live (backend/tools/
# db_bridge.py) is now the single entry point for both live and uploaded TB+Grouping
# files, using the native auto-detecting parser (input_dispatch.parse_tb_input) end to
# end. preview_excel_data below is unaffected -- it's a raw structural preview only,
# independent of the classification pipeline.


@pipeline_tool("preview_excel_data", domain="input")
def preview_excel_data(excel_path: str, is_grouping: bool = False, output_dir: str = None):
    """Reads the given Excel file to provide raw columns and sample structural headers to the Agent."""
    errors = []
    artifacts = []

    file_path = Path(excel_path)
    if not file_path.exists():
        raise PipelineFileError(str(file_path), "Excel/CSV input")

    try:
        if file_path.suffix.lower() == ".csv":
            df = pl.read_csv(file_path, has_header=False, infer_schema_length=None, encoding="utf8-lossy")
        else:
            df = pl.read_excel(
                file_path,
                sheet_id=1,
                has_header=False,
                drop_empty_rows=False,
                drop_empty_cols=False,
                infer_schema_length=None,
                read_options={"skip_rows": 0},
            )
        df = _stringify_grid(df)

        non_null_cols = [c for c in df.columns if df[c].null_count() < df.height]
        if non_null_cols:
            df = df.select(non_null_cols)

        found = _find_header(df, is_grouping)
        if found:
            header_idx, two_row = found
            header = [_norm___ingest_shared(v) for v in df.row(header_idx)]
            if two_row:
                sub = [_norm___ingest_shared(v) for v in df.row(header_idx + 1)]
                top = _ffill(header)
                width = max(len(header), len(sub))
                merged = []
                for i in range(width):
                    raw_top = header[i] if i < len(header) else ""
                    sub_i = sub[i] if i < len(sub) else ""
                    if not raw_top and not sub_i:
                        merged.append("")
                        continue
                    top_i = top[i] if i < len(top) else ""
                    merged.append(" ".join(x for x in (top_i, sub_i) if x).strip())
                df = df.rename(dict(zip(df.columns, _dedupe_names(merged))))
            else:
                df = df.rename(dict(zip(df.columns, _dedupe_names(header))))
        else:
            df, _generated = _generate_headers_if_missing(df)

        df = _normalize_duplicate_columns(df)
        columns = df.columns

        # If this is the grouping file, try to extract structural headers
        headers = []
        if is_grouping:
            for col in df.columns:
                if isinstance(col, str) and any(x in col.lower() for x in ["name", "desc", "head", "item"]):
                    unique_vals = df[col].drop_nulls().unique().to_list()
                    for val in unique_vals:
                        val_str = str(val).strip()
                        if val_str and not val_str.isdigit():
                            if val_str not in headers:
                                headers.append(val_str)
                            if len(headers) >= 100:
                                break
                    if headers:
                        break

        sample_rows = []
        for row in df.head(10).iter_rows(named=True):
            sample_rows.append({k: ("" if v is None or str(v) in ("nan", "None") else str(v)) for k, v in row.items()})

        data = {
            "columns": columns,
            "sample_headers": headers,
            "sample_rows": sample_rows,
        }

        out_dir = resolve_output_dir(output_dir)
        os.makedirs(out_dir, exist_ok=True)
        out_file = out_dir / f"preview_{file_path.stem}.json"
        write_json_atomic(data, out_file, indent=4)

        artifacts.append(str(out_file.resolve()))

        return {
            "execution_status": "SUCCESS",
            "pipeline_status": "SUCCESS",
            "message": "Preview completed successfully.",
            "artifacts": artifacts,
            "errors": errors,
        }
    except PipelineFileError:
        raise
    except Exception as e:
        errors.append(log_and_redact_exception("preview_excel_data", e, error_type="ParseError"))
        return {"execution_status": "FAILED", "errors": errors, "message": "Failed to preview file."}
