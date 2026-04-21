use pyo3::prelude::*;
use sha2::{Sha256, Digest};

/// High-performance scoring engine written in Rust.
/// Called from Python via PyO3 for the hot path (scoring + hashing).

#[pyfunction]
/// Compute a dedup hash from title + domain (SHA-256, first 32 hex chars)
fn compute_hash(domain: &str, title: &str) -> String {
    let norm = format!("{}|{}", domain.to_lowercase().replace("www.", ""), title.to_lowercase());
    let truncated: String = norm.chars().take(200).collect();
    let mut hasher = Sha256::new();
    hasher.update(truncated.as_bytes());
    let result = hasher.finalize();
    result.iter().take(16).map(|b| format!("{:02x}", b)).collect()
}

#[pyfunction]
/// Pure Rust scoring — no LLM needed for the numeric part.
/// Returns (score, breakdown_string)
fn calculate_score(
    is_free: bool,
    is_open_source: bool,
    credits_value_usd: i32,
    is_new_model: bool,
    days_ago: i32,
    upvotes: i32,
) -> (i32, String) {
    let mut score: i32 = 0;
    let mut parts: Vec<String> = Vec::new();

    // Free / OSS signal — highest weight
    if is_free || is_open_source {
        score += 4;
        parts.push("free/OSS +4".to_string());
    }

    // Free credits — the "expensive but free" signal
    if credits_value_usd >= 20 {
        score += 4;
        parts.push(format!("credits ${} +4", credits_value_usd));
    } else if credits_value_usd >= 5 {
        score += 2;
        parts.push(format!("credits ${} +2", credits_value_usd));
    }

    // New model release
    if is_new_model {
        score += 3;
        parts.push("new model +3".to_string());
    }

    // Recency — strict filter
    if days_ago < 7 {
        score += 2;
        parts.push(format!("{}d old +2", days_ago));
    } else if days_ago < 14 {
        score += 1;
        parts.push(format!("{}d old +1", days_ago));
    }
    // Items older than 14 days get NO recency bonus (hard reject signal)

    // Community validation boost
    if upvotes >= 100 {
        score += 1;
        parts.push(format!("{} upvotes +1", upvotes));
    }

    if parts.is_empty() {
        parts.push("no signals".to_string());
    }

    (score, parts.join(", "))
}

#[pyfunction]
/// Check if text contains any of the given keywords (case-insensitive, Rust fast path)
fn contains_any_keyword(text: &str, keywords: Vec<String>) -> bool {
    let lower = text.to_lowercase();
    for kw in &keywords {
        if lower.contains(kw.as_str()) {
            return true;
        }
    }
    false
}

/// Module registration
#[pymodule]
fn scorer_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compute_hash, m)?)?;
    m.add_function(wrap_pyfunction!(calculate_score, m)?)?;
    m.add_function(wrap_pyfunction!(contains_any_keyword, m)?)?;
    Ok(())
}
