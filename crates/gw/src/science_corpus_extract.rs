use std::fs;
use std::path::Path;
use sha2::{Digest, Sha256};

pub fn extract(manifest_path: &Path, out: &Path, captured_at: &str) -> Result<(), String> {
    if captured_at.is_empty() || !captured_at.ends_with('Z') { return Err("captured-at must be RFC3339".into()); }
    let bytes = fs::read(manifest_path).map_err(|e| e.to_string())?;
    let manifest: serde_json::Value = serde_json::from_slice(&bytes).map_err(|e| format!("invalid manifest: {e}"))?;
    let extractor = manifest.get("extractor").ok_or("missing extractor version")?;
    let name = extractor.get("name").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).ok_or("missing extractor name")?;
    let version = extractor.get("version").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).ok_or("missing extractor version")?;
    let originals = manifest.get("originals").and_then(|v| v.as_array()).ok_or("missing originals")?;
    let records = manifest.get("records").and_then(|v| v.as_array()).ok_or("missing synthetic records")?;
    let base = manifest_path.parent().unwrap_or(Path::new("."));
    let mut results = Vec::new();
    let mut units = Vec::new();
    for original in originals {
        let path = original.get("path").and_then(|v| v.as_str()).ok_or("original path missing")?;
        let expected = original.get("sha256").and_then(|v| v.as_str()).ok_or("original hash missing")?;
        let data = fs::read(base.join(path)).map_err(|e| format!("original bytes unavailable: {e}"))?;
        let actual = format!("{:x}", Sha256::digest(&data));
        if actual != expected || data.len() as u64 != original.get("bytes").and_then(|v| v.as_u64()).unwrap_or(u64::MAX) { return Err("original hash or byte length changed".into()); }
        let typ = original.get("media_type").and_then(|v| v.as_str()).unwrap_or("");
        if typ == "video" || typ == "audio" { results.push(serde_json::json!({"path":path,"status":"pending","reason":"timestamped local transcript adapter unavailable"})); continue; }
        let pages = original.get("pages").and_then(|v| v.as_u64()).ok_or("incomplete declared pages")? as u32;
        let mut page_results = Vec::new();
        for page in 1..=pages {
            let rec = records.iter().find(|r| r.get("path").and_then(|v| v.as_str()) == Some(path) && r.get("page").and_then(|v| v.as_u64()) == Some(page as u64)).ok_or("incomplete declared pages")?;
            let status = rec.get("status").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).ok_or("missing page status")?;
            let mut item = serde_json::json!({"page":page,"status":status});
            if let Some(text) = rec.get("text").and_then(|v| v.as_str()) {
                if text.is_empty() { return Err("invalid/unresolved page locator".into()); }
                let end = text.chars().count() as u64;
                item["unit_id"] = serde_json::Value::String(format!("{}:{}", expected, page));
                item["offset_start"] = serde_json::json!(0); item["offset_end"] = serde_json::json!(end);
                units.push(serde_json::json!({"id":format!("{}:{}", expected, page),"source_hash":expected,"text":text,"locator":{"page":page,"offset_start":0,"offset_end":end}}));
            } else if status != "failed" && status != "ocr_required" && status != "retry" { return Err("failed page silently omitted or missing text".into()); }
            page_results.push(item);
        }
        results.push(serde_json::json!({"path":path,"source_hash":expected,"pages":page_results}));
    }
    let identity = format!("{:x}", Sha256::digest(&bytes));
    let artifact = serde_json::json!({"schema":"gw.science.corpus.extraction","version":1,"manifest_identity":{"sha256":identity},"captured_at":captured_at,"extractor":{"name":name,"version":version},"results":results,"units":units});
    fs::create_dir_all(out).map_err(|e| e.to_string())?;
    fs::write(out.join("extraction.json"), serde_json::to_string_pretty(&artifact).unwrap() + "\n").map_err(|e| e.to_string())
}
