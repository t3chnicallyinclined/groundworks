use sha2::{Digest, Sha256};
use std::{fs, process::Command};

fn run(root: &std::path::Path, manifest: serde_json::Value, name: &str) -> std::process::Output {
    fs::write(
        root.join("manifest.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
    Command::new(env!("CARGO_BIN_EXE_gw"))
        .args([
            "corpus",
            "extract",
            "--manifest",
            root.join("manifest.json").to_str().unwrap(),
            "--out",
            root.join(name).to_str().unwrap(),
            "--captured-at",
            "2026-09-21T00:00:00Z",
        ])
        .output()
        .unwrap()
}
fn fixture(root: &std::path::Path, media: &str, records: serde_json::Value) -> serde_json::Value {
    let b = b"synthetic";
    fs::write(root.join("a.pdf"), b).unwrap();
    serde_json::json!({"source":{"supplied_path":root},"extractor":{"name":"synthetic","version":"1"},"originals":[{"path":"a.pdf","bytes":b.len(),"sha256":format!("{:x}", Sha256::digest(b)),"media_type":media,"pages":1}],"records":records})
}
#[test]
fn synthetic_subprocess_validation_and_determinism() {
    let root = std::env::temp_dir().join(format!("gw-extract-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let good = fixture(
        &root,
        "application/pdf",
        serde_json::json!([{"path":"a.pdf","page":1,"status":"extracted","text":"hello","locator":{"page":1,"offset_start":0,"offset_end":5}}]),
    );
    let first = run(&root, good.clone(), "one");
    assert!(first.status.success());
    let bytes = fs::read(root.join("one/extraction.json")).unwrap();
    assert!(run(&root, good, "two").status.success());
    assert_eq!(bytes, fs::read(root.join("two/extraction.json")).unwrap());
    let mut changed = fixture(&root, "application/pdf", serde_json::json!([]));
    changed["originals"][0]["sha256"] = serde_json::json!("00");
    assert!(!run(&root, changed, "changed").status.success());
    let mut missing = fixture(&root, "application/pdf", serde_json::json!([]));
    missing["extractor"] = serde_json::json!({"name":"synthetic"});
    assert!(!run(&root, missing, "missing").status.success());
    let outside = root.parent().unwrap().join("gw-extract-outside.pdf");
    fs::write(&outside, b"synthetic").unwrap();
    let mut escaped = fixture(
        &root,
        "application/pdf",
        serde_json::json!([{"path":"../gw-extract-outside.pdf","page":1,"status":"extracted","text":"hello","locator":{"page":1,"offset_start":0,"offset_end":5}}]),
    );
    escaped["originals"][0]["path"] = serde_json::json!("../gw-extract-outside.pdf");
    let escaped = run(&root, escaped, "escaped");
    assert!(!escaped.status.success());
    assert!(String::from_utf8_lossy(&escaped.stderr).contains("root-relative"));
    for records in [
        serde_json::json!([]),
        serde_json::json!([{"path":"a.pdf","page":1,"status":"extracted","text":"hello"}]),
    ] {
        assert!(
            !run(&root, fixture(&root, "application/pdf", records), "bad")
                .status
                .success()
        );
    }
    let failed = run(
        &root,
        fixture(
            &root,
            "application/pdf",
            serde_json::json!([{"path":"a.pdf","page":1,"status":"failed"}]),
        ),
        "failed",
    );
    assert!(failed.status.success());
    assert!(
        String::from_utf8_lossy(&fs::read(root.join("failed/extraction.json")).unwrap())
            .contains("\"status\": \"failed\"")
    );
    let video = fixture(&root, "video/mp4", serde_json::json!([]));
    let out = run(&root, video, "video");
    assert!(out.status.success());
    assert!(
        String::from_utf8_lossy(&fs::read(root.join("video/extraction.json")).unwrap())
            .contains("pending")
    );
    let _ = fs::remove_dir_all(root);
    let _ = fs::remove_file(outside);
}
