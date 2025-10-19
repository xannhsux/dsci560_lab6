#!/usr/bin/env python3
"""
Generate text embeddings for well documents using Bag-of-Words and
Word2Vec-based clustering.

This script pulls the textual fields that were parsed from the PDF completion
reports, constructs a document per well, and then produces two kinds of feature
representations:

1. Bag-of-Words: standard term-frequency vectors using scikit-learn's
   CountVectorizer.
2. Bag-of-Centroids: cluster histograms built by training a Word2Vec model on
   the corpus, clustering the learned word vectors, and aggregating the token
   counts per cluster.

Typical usage (after the MySQL database has been populated via the data
pipeline):

    python -m src.text_embeddings --output-dir outputs/

The script will create the requested output directory if it does not already
exist and export three CSV files:

* bag_of_words.csv          – document-term matrix
* word2vec_clusters.csv     – bag-of-centroids per well
* cluster_top_terms.csv     – top words per Word2Vec cluster
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np
import pandas as pd
from gensim.models import Word2Vec
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import CountVectorizer
from sqlalchemy.orm import selectinload

from db_utils import Well, get_session

# -----------------------------------------------------------------------------
# Logging configuration
# -----------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9']+")


def tokenize(text: str) -> List[str]:
    """Lowercase tokenisation that filters out very short tokens."""
    if not text:
        return []
    return [
        token
        for token in (match.group(0).lower() for match in TOKEN_PATTERN.finditer(text))
        if len(token) > 2
    ]


def fetch_documents() -> pd.DataFrame:
    """
    Pull relevant textual fields for each well from the database and return
    them as a DataFrame with columns ``well_id``, ``api``, ``document``,
    and ``tokens``.
    """
    session = get_session()
    try:
        wells: Sequence[Well] = (
            session.query(Well)
            .options(selectinload(Well.stimulations))
            .order_by(Well.id)
            .all()
        )

        documents: List[Dict[str, object]] = []

        for well in wells:
            parts: List[str] = []
            for value in (
                well.well_name,
                well.operator,
                well.job_type,
                well.county_state,
                well.well_status,
                well.well_type,
                well.closest_city,
                well.shl,
                well.datum,
            ):
                if value:
                    parts.append(str(value))

            # Include stimulation detail narrative text (if any) to enrich corpus.
            for stimulation in well.stimulations:
                if stimulation.details:
                    parts.append(stimulation.details)

            document_text = " ".join(part.strip() for part in parts if part).strip()
            if not document_text:
                continue

            documents.append(
                {
                    "well_id": well.id,
                    "api": well.api,
                    "document": document_text,
                    "tokens": tokenize(document_text),
                }
            )

        if not documents:
            raise ValueError(
                "No textual documents were found. Verify that the database is populated."
            )

        df = pd.DataFrame(documents)
        logger.info("Collected %d documents for embedding generation.", len(df))
        avg_tokens = np.mean([len(tokens) for tokens in df["tokens"]])
        logger.info("Average token count per document: %.1f", avg_tokens)
        return df
    finally:
        session.close()


def compute_bag_of_words(
    docs: pd.DataFrame, *, max_features: int, min_df: int
) -> Tuple[pd.DataFrame, List[str]]:
    """Construct a Bag-of-Words document-term matrix."""
    vectorizer = CountVectorizer(
        lowercase=True,
        stop_words="english",
        min_df=min_df,
        max_features=max_features if max_features > 0 else None,
    )
    matrix = vectorizer.fit_transform(docs["document"])
    feature_names = vectorizer.get_feature_names_out().tolist()
    logger.info(
        "Bag-of-Words vocabulary size: %d (min_df=%d, max_features=%s)",
        len(feature_names),
        min_df,
        "None" if max_features <= 0 else max_features,
    )

    bow_array = matrix.toarray().astype(np.int32, copy=False)
    bow_df = pd.DataFrame(bow_array, columns=feature_names)
    bow_df.insert(0, "api", docs["api"].values)
    bow_df.insert(0, "well_id", docs["well_id"].values)
    return bow_df, feature_names


def compute_word2vec_clusters(
    docs: pd.DataFrame,
    *,
    vector_size: int,
    window: int,
    min_count: int,
    epochs: int,
    num_clusters: int,
    random_state: int,
    workers: int,
    skip_gram: bool,
) -> Tuple[pd.DataFrame, Dict[str, int], Dict[int, List[Tuple[str, int]]]]:
    """
    Train a Word2Vec model, cluster the vocabulary, and build per-document
    histograms over the resulting clusters (bag-of-centroids).
    """
    sentences = docs["tokens"].tolist()
    if not any(sentence for sentence in sentences):
        raise ValueError("Tokenised documents are empty; cannot train Word2Vec.")

    workers = max(1, workers)
    sg = 1 if skip_gram else 0
    model = Word2Vec(
        sentences=sentences,
        vector_size=vector_size,
        window=window,
        min_count=min_count,
        workers=workers,
        sg=sg,
        epochs=epochs,
    )

    vocab = model.wv.index_to_key
    if not vocab:
        raise ValueError(
            "Word2Vec vocabulary is empty. Adjust --min-count or provide more text."
        )

    if num_clusters > len(vocab):
        logger.warning(
            "Requested %d clusters but only %d vocabulary terms available; "
            "reducing cluster count.",
            num_clusters,
            len(vocab),
        )
        num_clusters = max(1, len(vocab))

    vectors = np.vstack([model.wv[word] for word in vocab])
    kmeans = KMeans(
        n_clusters=num_clusters,
        random_state=random_state,
        n_init=10,
    )
    cluster_ids = kmeans.fit_predict(vectors)
    cluster_lookup = dict(zip(vocab, cluster_ids))

    # Pre-compute token frequencies for later reporting.
    token_frequency: Counter[str] = Counter(
        token for sentence in sentences for token in sentence
    )

    centroid_names: Dict[int, List[Tuple[str, int]]] = defaultdict(list)
    for word, cluster_id in cluster_lookup.items():
        centroid_names[cluster_id].append((word, token_frequency[word]))

    # Document-level histograms (normalised so each row sums to 1.0).
    doc_features: List[np.ndarray] = []
    for tokens in sentences:
        counts = Counter(cluster_lookup[token] for token in tokens if token in cluster_lookup)
        vector = np.zeros(num_clusters, dtype=np.float32)
        total = sum(counts.values())
        if total:
            for cluster_id, count in counts.items():
                vector[cluster_id] = count / total
        doc_features.append(vector)

    embedding_df = pd.DataFrame(
        doc_features, columns=[f"cluster_{idx}" for idx in range(num_clusters)]
    )
    embedding_df.insert(0, "api", docs["api"].values)
    embedding_df.insert(0, "well_id", docs["well_id"].values)

    # Sort cluster keywords by frequency (descending) then alphabetically.
    sorted_cluster_terms: Dict[int, List[Tuple[str, int]]] = {
        cluster_id: sorted(
            terms,
            key=lambda item: (-item[1], item[0]),
        )
        for cluster_id, terms in centroid_names.items()
    }

    logger.info(
        "Trained Word2Vec (vector_size=%d, window=%d, min_count=%d, epochs=%d, sg=%s) "
        "and clustered into %d centroids.",
        vector_size,
        window,
        min_count,
        epochs,
        "skip-gram" if skip_gram else "CBOW",
        num_clusters,
    )

    return embedding_df, cluster_lookup, sorted_cluster_terms


def export_cluster_keywords(
    cluster_terms: Dict[int, List[Tuple[str, int]]],
    *,
    top_k: int,
) -> pd.DataFrame:
    """Create a tidy DataFrame describing the top tokens for each cluster."""
    records: List[Dict[str, object]] = []
    for cluster_id, terms in sorted(cluster_terms.items()):
        for rank, (word, count) in enumerate(terms[:top_k], start=1):
            records.append(
                {"cluster": cluster_id, "rank": rank, "token": word, "count": int(count)}
            )

    if not records:
        return pd.DataFrame(columns=["cluster", "rank", "token", "count"])

    return pd.DataFrame(records)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Bag-of-Words and Word2Vec embeddings for well documents."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="Directory where the CSV files will be written (default: ./outputs)",
    )
    parser.add_argument(
        "--max-features",
        type=int,
        default=1000,
        help="Maximum vocabulary size for Bag-of-Words (0 means unlimited).",
    )
    parser.add_argument(
        "--min-df",
        type=int,
        default=2,
        help="Minimum document frequency for Bag-of-Words vocabulary inclusion.",
    )
    parser.add_argument(
        "--vector-size",
        type=int,
        default=100,
        help="Dimensionality of Word2Vec embeddings.",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=5,
        help="Word2Vec context window size.",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=2,
        help="Minimum frequency threshold for Word2Vec vocabulary.",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=15,
        help="Training epochs for Word2Vec.",
    )
    parser.add_argument(
        "--clusters",
        type=int,
        default=20,
        help="Number of KMeans clusters for grouping Word2Vec embeddings.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Top tokens per cluster to export for inspection.",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="Random seed for KMeans clustering.",
    )
    parser.add_argument(
        "--skip-gram",
        action="store_true",
        help="Use the skip-gram variant of Word2Vec instead of CBOW.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 1,
        help="Number of worker threads for Word2Vec training.",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="",
        help="Optional prefix for output filenames.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(name)s: %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    configure_logging(args.verbose)

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Writing embedding artefacts to %s", output_dir.resolve())

    docs = fetch_documents()

    bow_df, bow_features = compute_bag_of_words(
        docs, max_features=args.max_features, min_df=args.min_df
    )
    bow_filename = output_dir / f"{args.prefix}bag_of_words.csv"
    bow_df.to_csv(bow_filename, index=False)
    logger.info(
        "Saved Bag-of-Words matrix with %d documents and %d features to %s",
        bow_df.shape[0],
        bow_df.shape[1] - 2,
        bow_filename,
    )

    w2v_df, cluster_lookup, cluster_terms = compute_word2vec_clusters(
        docs,
        vector_size=args.vector_size,
        window=args.window,
        min_count=args.min_count,
        epochs=args.epochs,
        num_clusters=args.clusters,
        random_state=args.random_state,
        workers=args.workers,
        skip_gram=args.skip_gram,
    )
    w2v_filename = output_dir / f"{args.prefix}word2vec_clusters.csv"
    w2v_df.to_csv(w2v_filename, index=False)
    logger.info(
        "Saved Word2Vec cluster features (shape=%s) to %s",
        w2v_df.shape,
        w2v_filename,
    )

    cluster_keywords_df = export_cluster_keywords(cluster_terms, top_k=args.top_k)
    keywords_filename = output_dir / f"{args.prefix}cluster_top_terms.csv"
    cluster_keywords_df.to_csv(keywords_filename, index=False)
    logger.info("Saved cluster keyword summary to %s", keywords_filename)

    logger.info(
        "Completed embedding generation – Bag-of-Words features: %d, "
        "Word2Vec clusters: %d",
        len(bow_features),
        len(cluster_terms),
    )


if __name__ == "__main__":
    main()

