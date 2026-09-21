//! gw command line.
use std::io::Read;
mod science_corpus_freeze;`nmod science_corpus_extract;
fn main() {
    let mut args = std::env::args().skip(1);
    match args.next().as_deref() {`n        Some("corpus") if args.next().as_deref() == Some("extract") => {`n            let mut m=None; let mut o=None; let mut t=None;`n            while let Some(a)=args.next() { match a.as_str() { "--manifest"=>m=args.next(), "--out"=>o=args.next(), "--captured-at"=>t=args.next(), _=>{eprintln!("gw: unknown argument {a}");std::process::exit(2)} } }`n            if let Err(e)=science_corpus_extract::extract(std::path::Path::new(m.as_deref().unwrap_or("")),std::path::Path::new(o.as_deref().unwrap_or("")),t.as_deref().unwrap_or("")){eprintln!("gw: {e}");std::process::exit(2)}`n        }
        Some("corpus") if args.next().as_deref() == Some("freeze") => {
            let mut c = None;
            let mut s = None;
            let mut o = None;
            let mut t = String::from("1970-01-01T00:00:00Z");
            let mut includes = Vec::new();
            while let Some(a) = args.next() {
                match a.as_str() {
                    "--collection" => c = args.next(),
                    "--source" => s = args.next(),
                    "--out" => o = args.next(),
                    "--capture-time" | "--captured-at" => t = args.next().unwrap_or_default(),
                    "--include" => includes.push(args.next().unwrap_or_default()),
                    _ => {
                        eprintln!("gw: unknown argument {a}");
                        std::process::exit(2)
                    }
                }
            }
            let r = science_corpus_freeze::freeze(
                c.as_deref().unwrap_or(""),
                std::path::Path::new(s.as_deref().unwrap_or("")),
                std::path::Path::new(o.as_deref().unwrap_or("")),
                &t,
                &includes,
            );
            if let Err(e) = r {
                eprintln!("gw: {e}");
                std::process::exit(2)
            }
        }
        Some("--oracle") => println!("{} {}", gw_core::ORACLE_TAG, gw_core::ORACLE_COMMIT),
        Some("--split-statements") => {
            let mut i = String::new();
            if std::io::stdin().read_to_string(&mut i).is_err() {
                std::process::exit(2)
            };
            println!(
                "{}",
                serde_json::to_string(&gw_core::db::split_statements(&i)).unwrap()
            )
        }
        Some(x) => {
            eprintln!("gw: unknown argument {x}");
            std::process::exit(2)
        }
        None => println!("gw: no engine yet"),
    }
}

