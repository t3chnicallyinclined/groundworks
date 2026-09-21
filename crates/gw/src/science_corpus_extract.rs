use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;

const NON_EXTRACTED: [&str; 3] = ["failed", "retry", "ocr_required"];

pub fn extract(manifest_path: &Path, out: &Path, captured_at: &str) -> Result<(), String> {
    if captured_at.is_empty() || !captured_at.ends_with('Z') {
        return Err("captured-at must be RFC3339".into());
    }
    let bytes = fs::read(manifest_path).map_err(|e| e.to_string())?;
    let manifest: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("invalid manifest: {e}"))?;
    let extractor = manifest
        .get("extractor")
        .ok_or("missing extractor version")?;
    let name = extractor
        .get("name")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .ok_or("missing extractor name")?;
    let version = extractor
        .get("version")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .ok_or("missing extractor version")?;
    let source = manifest.get("source").ok_or("missing source provenance")?;
    let supplied_path = source
        .get("supplied_path")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .ok_or("missing source supplied_path")?;
    let source_root = Path::new(supplied_path);
    if !source_root.is_dir() {
        return Err("invalid source supplied_path".into());
    }
    let originals = manifest
        .get("originals")
        .and_then(|v| v.as_array())
        .ok_or("missing originals")?;
    let records = manifest
        .get("records")
        .and_then(|v| v.as_array())
        .ok_or("missing synthetic records")?;
    let mut results = Vec::new();
    let mut units = Vec::new();
    for original in originals {
        let path = original
            .get("path")
            .and_then(|v| v.as_str())
            .ok_or("original path missing")?;
        let expected = original
            .get("sha256")
            .and_then(|v| v.as_str())
            .ok_or("original hash missing")?;
        let data = fs::read(source_root.join(path))
            .map_err(|e| format!("original bytes unavailable: {e}"))?;
        let actual = format!("{:x}", Sha256::digest(&data));
        let length = original
            .get("bytes")
            .and_then(|v| v.as_u64())
            .ok_or("original byte length missing")?;
        if actual != expected || data.len() as u64 != length {
            return Err("original hash or byte length changed".into());
        }
        let typ = original
            .get("media_type")
            .and_then(|v| v.as_str())
            .ok_or("missing media type")?;
        if typ.starts_with("video/") {
            results.push(serde_json::json!({"path":path,"status":"pending","reason":"video extraction adapter unavailable"}));
            continue;
        }
        if typ != "application/pdf" {
            return Err(format!("unsupported media type: {typ}"));
        }
        let pages = original
            .get("pages")
            .and_then(|v| v.as_u64())
            .ok_or("incomplete declared pages")? as u32;
        let mut page_results = Vec::new();
        for page in 1..=pages {
            let matching: Vec<_> = records
                .iter()
                .filter(|r| {
                    r.get("path").and_then(|v| v.as_str()) == Some(path)
                        && r.get("page").and_then(|v| v.as_u64()) == Some(page as u64)
                })
                .collect();
            if matching.len() != 1 {
                return Err("each declared page must have exactly one record".into());
            }
            let rec = matching[0];
            let status = rec
                .get("status")
                .and_then(|v| v.as_str())
                .ok_or("missing page status")?;
            if status != "extracted" && !NON_EXTRACTED.contains(&status) {
                return Err("invalid page status".into());
            }
            let mut item = serde_json::json!({"page":page,"status":status});
            if status == "extracted" {
                let text = rec
                    .get("text")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .ok_or("extracted page missing text")?;
                let locator = rec
                    .get("locator")
                    .ok_or("invalid/unresolved page locator")?;
                let lp = locator
                    .get("page")
                    .and_then(|v| v.as_u64())
                    .ok_or("invalid/unresolved page locator")?;
                let start = locator
                    .get("offset_start")
                    .and_then(|v| v.as_u64())
                    .ok_or("invalid/unresolved page locator")?;
                let end = locator
                    .get("offset_end")
                    .and_then(|v| v.as_u64())
                    .ok_or("invalid/unresolved page locator")?;
                if lp != page as u64 || start >= end || end > text.chars().count() as u64 {
                    return Err("invalid/unresolved page locator".into());
                }
                let id = format!("{}:{}", expected, page);
                item["unit_id"] = serde_json::Value::String(id.clone());
                item["offset_start"] = serde_json::json!(start);
                item["offset_end"] = serde_json::json!(end);
                units.push(serde_json::json!({"id":id,"source_hash":expected,"text":text,"locator":{"page":page,"offset_start":start,"offset_end":end}}));
            }
            page_results.push(item);
        }
        if records.iter().any(|r| {
            r.get("path").and_then(|v| v.as_str()) == Some(path)
                && r.get("page")
                    .and_then(|v| v.as_u64())
                    .map_or(true, |p| p == 0 || p > pages as u64)
        }) {
            return Err("record references unknown page".into());
        }
        results.push(serde_json::json!({"path":path,"source_hash":expected,"pages":page_results}));
    }
    let identity = format!("{:x}", Sha256::digest(&bytes));
    let artifact = serde_json::json!({"schema":"gw.science.corpus.extraction","version":1,"manifest_identity":{"sha256":identity},"captured_at":captured_at,"extractor":{"name":name,"version":version},"results":results,"units":units});
    fs::create_dir_all(out).map_err(|e| e.to_string())?;
    fs::write(
        out.join("extraction.json"),
        serde_json::to_string_pretty(&artifact).unwrap() + "\n",
    )
    .map_err(|e| e.to_string())
}
