use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Component, Path, PathBuf};

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
    let source_root =
        fs::canonicalize(supplied_path).map_err(|_| "invalid source supplied_path")?;
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
        let source_path = resolve_original_path(&source_root, path)?;
        let data = fs::read(source_path).map_err(|e| format!("original bytes unavailable: {e}"))?;
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

fn resolve_original_path(root: &Path, value: &str) -> Result<PathBuf, String> {
    let path = Path::new(value);
    if path.is_absolute()
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        return Err("original path must be root-relative".into());
    }
    let resolved = fs::canonicalize(root.join(path))
        .map_err(|e| format!("original bytes unavailable: {e}"))?;
    if !resolved.starts_with(root) || !resolved.is_file() {
        return Err("original path escapes source root".into());
    }
    Ok(resolved)
}

pub fn extract_native(
    manifest_path: &Path,
    out: &Path,
    captured_at: &str,
    pdfinfo: &Path,
    pdftotext: &Path,
) -> Result<(), String> {
    if captured_at.is_empty() || !captured_at.ends_with('Z') {
        return Err("captured-at must be RFC3339".into());
    }
    if !pdfinfo.is_file() || !pdftotext.is_file() {
        return Err("supplied extraction tools must be executable files".into());
    }
    let bytes = fs::read(manifest_path).map_err(|e| e.to_string())?;
    let manifest: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("invalid manifest: {e}"))?;
    let source = manifest.get("source").ok_or("missing source provenance")?;
    let root = fs::canonicalize(
        source
            .get("supplied_path")
            .and_then(|v| v.as_str())
            .ok_or("missing source supplied_path")?,
    )
    .map_err(|_| "invalid source supplied_path")?;
    if !root.is_dir() {
        return Err("invalid source supplied_path".into());
    }
    let out_abs = if out.exists() {
        fs::canonicalize(out).map_err(|e| e.to_string())?
    } else {
        out.parent()
            .map(fs::canonicalize)
            .transpose()
            .map_err(|e| e.to_string())?
            .unwrap_or_else(|| PathBuf::from("."))
            .join(out.file_name().ok_or("invalid output")?)
    };
    if out_abs.starts_with(&root) {
        return Err("output directory must not be inside source root".into());
    }
    let originals = manifest
        .get("originals")
        .and_then(|v| v.as_array())
        .ok_or("missing originals")?;
    let tool_version = |p: &Path| -> String {
        std::process::Command::new(p)
            .arg("--version")
            .output()
            .ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default()
    };
    let tools = serde_json::json!({"pdfinfo":{"executable":pdfinfo.to_string_lossy(),"version":tool_version(pdfinfo)},"pdftotext":{"executable":pdftotext.to_string_lossy(),"version":tool_version(pdftotext)}});
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
        let source_path = resolve_original_path(&root, path)?;
        let data = fs::read(&source_path).map_err(|e| e.to_string())?;
        if format!("{:x}", Sha256::digest(&data)) != expected
            || original.get("bytes").and_then(|v| v.as_u64()) != Some(data.len() as u64)
        {
            return Err("original hash or byte length changed".into());
        }
        let typ = original
            .get("media_type")
            .and_then(|v| v.as_str())
            .ok_or("missing media type")?;
        if typ == "video/mp4" {
            results.push(serde_json::json!({"path":path,"status":"pending","reason":"video extraction adapter unavailable"}));
            continue;
        }
        if typ != "application/pdf" {
            return Err(format!("unsupported media type: {typ}"));
        }
        let info = std::process::Command::new(pdfinfo)
            .arg(&source_path)
            .output()
            .map_err(|e| e.to_string())?;
        if !info.status.success() {
            return Err("pdfinfo failed".into());
        }
        let pages = String::from_utf8_lossy(&info.stdout)
            .lines()
            .find_map(|l| {
                l.strip_prefix("Pages:")
                    .and_then(|x| x.trim().parse::<u32>().ok())
            })
            .ok_or("invalid page count")?;
        if pages == 0 {
            return Err("invalid page count".into());
        }
        let mut page_results = Vec::new();
        for page in 1..=pages {
            let r = std::process::Command::new(pdftotext)
                .args([
                    "-f",
                    &page.to_string(),
                    "-l",
                    &page.to_string(),
                    source_path.to_str().ok_or("invalid source path")?,
                    "-",
                ])
                .output();
            let item = match r {
                Ok(o) if o.status.success() => {
                    let text = String::from_utf8_lossy(&o.stdout).trim().to_string();
                    if text.is_empty() {
                        serde_json::json!({"page":page,"status":"ocr_required"})
                    } else {
                        let end = text.chars().count() as u64;
                        let id = format!("{}:{}", expected, page);
                        units.push(serde_json::json!({"id":id,"source_hash":expected,"text":text,"locator":{"page":page,"offset_start":0,"offset_end":end}}));
                        serde_json::json!({"page":page,"status":"extracted","unit_id":id,"offset_start":0,"offset_end":end})
                    }
                }
                _ => serde_json::json!({"page":page,"status":"retry","reason":"pdftotext failed"}),
            };
            page_results.push(item);
        }
        results.push(serde_json::json!({"path":path,"source_hash":expected,"pages":page_results}));
    }
    let artifact = serde_json::json!({"schema":"gw.science.corpus.extraction","version":1,"manifest_identity":{"sha256":format!("{:x}",Sha256::digest(&bytes))},"captured_at":captured_at,"tools":tools,"results":results,"units":units});
    fs::create_dir_all(&out_abs).map_err(|e| e.to_string())?;
    fs::write(
        out_abs.join("extraction.json"),
        serde_json::to_string_pretty(&artifact).unwrap() + "\n",
    )
    .map_err(|e| e.to_string())
}
