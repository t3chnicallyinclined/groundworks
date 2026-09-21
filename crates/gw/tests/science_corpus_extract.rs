use sha2::{Digest, Sha256};
use std::{fs, process::Command};
#[test]
fn deterministic_synthetic_extraction() {
    let d = std::env::temp_dir().join("gw-extract-test");
    let _ = fs::remove_dir_all(&d);
    fs::create_dir_all(&d).unwrap();
    let b = b"synthetic";
    fs::write(d.join("a.pdf"), b).unwrap();
    let h = format!("{:x}", Sha256::digest(b));
    let m = serde_json::json!({"extractor":{"name":"synthetic","version":"1"},"originals":[{"path":"a.pdf","bytes":b.len(),"sha256":h,"media_type":"pdf","pages":1}],"records":[{"path":"a.pdf","page":1,"status":"extracted","text":"hello"}]});
    fs::write(d.join("m.json"), serde_json::to_vec(&m).unwrap()).unwrap();
    let out = d.join("out");
    let exe = env!("CARGO_BIN_EXE_gw");
    let s = Command::new(exe)
        .args([
            "corpus",
            "extract",
            "--manifest",
            d.join("m.json").to_str().unwrap(),
            "--out",
            out.to_str().unwrap(),
            "--captured-at",
            "2026-09-21T00:00:00Z",
        ])
        .status()
        .unwrap();
    assert!(s.success());
    let first = fs::read(out.join("extraction.json")).unwrap();
    let _ = Command::new(exe)
        .args([
            "corpus",
            "extract",
            "--manifest",
            d.join("m.json").to_str().unwrap(),
            "--out",
            out.to_str().unwrap(),
            "--captured-at",
            "2026-09-21T00:00:00Z",
        ])
        .status();
    assert_eq!(first, fs::read(out.join("extraction.json")).unwrap());
}
