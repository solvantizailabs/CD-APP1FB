import datetime
import os
import uuid
import hashlib
import json
import logging
import pickle
from typing import List, Dict, Optional

from qdrant_client import QdrantClient as QC, models
from backend.app.services.llm.openai_client import OPENAI_MODEL, create_client
from pypdf import PdfReader
import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION_NAME", "textbooks_v2")
EMBEDDING_TYPE = os.environ.get("EMBEDDING_TYPE", "openai").lower()
if EMBEDDING_TYPE == "openai" and not os.getenv("OPENAI_API_KEY"):
    print("[Qdrant Warning] OPENAI_API_KEY not found. Falling back to local Sentence-Transformer.")
    EMBEDDING_TYPE = "local"

if EMBEDDING_TYPE == "openai":
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
else:
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

# --- GLOBALS (initialized by initialize()) ---
client: Optional[QC] = None
local_embedder = None
openai_client = None
generation_model_name: str = os.environ.get("OPENAI_MODEL", OPENAI_MODEL)
bm25_indices: Dict[str, BM25Okapi] = {}
book_corpus: Dict[str, List[Dict]] = {}


class FastEmbedWrapper:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding
        full_model_name = model_name
        if "/" not in model_name:
            full_model_name = f"sentence-transformers/{model_name}"
        self.model = TextEmbedding(model_name=full_model_name)
        self.model_name = model_name

    def encode(self, texts):
        if isinstance(texts, str):
            res = list(self.model.embed([texts]))[0]
            return np.array(res, dtype=np.float32)
        else:
            res_list = list(self.model.embed(texts))
            return np.array(res_list, dtype=np.float32)

    def get_sentence_embedding_dimension(self) -> int:
        if "all-MiniLM-L6-v2" in self.model_name:
            return 384
        return 384


class OpenAIEmbedderWrapper:
    def __init__(self, model_name: str = "text-embedding-3-small"):
        self.model_name = model_name

    def encode(self, texts):
        from openai import OpenAI
        client_api = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        if isinstance(texts, str):
            response = client_api.embeddings.create(
                input=[texts],
                model=self.model_name
            )
            res = response.data[0].embedding
            return np.array(res, dtype=np.float32)
        else:
            response = client_api.embeddings.create(
                input=texts,
                model=self.model_name
            )
            res_list = [item.embedding for item in response.data]
            return np.array(res_list, dtype=np.float32)

    def get_sentence_embedding_dimension(self) -> int:
        if "text-embedding-3-small" in self.model_name:
            return 1536
        return 1536


def initialize():
    """
    Initialize models and Qdrant client. Called once at application startup.
    PRODUCTION MODE: Preserves existing data.
    """
    global client, local_embedder, openai_client, generation_model_name

    if EMBEDDING_TYPE == "openai":
        local_embedder = OpenAIEmbedderWrapper(EMBEDDING_MODEL)
    else:
        local_embedder = FastEmbedWrapper(EMBEDDING_MODEL)

    api_key = os.getenv("OPENAI_API_KEY")
    print(f"[DEBUG OPENAI KEY] Loaded API key: {'yes' if api_key else 'no'}")
    try:
        openai_client = create_client()
        from backend.app.utils.llm_tracker import instrument_client
        openai_client = instrument_client(openai_client)
        print("[Qdrant] OpenAI client initialized and instrumented successfully in qdrant_service.")
    except Exception as e:
        print(f"[Qdrant Warning] Error initializing OpenAI client: {e}")

    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")

    if not qdrant_url:
        raise ValueError("QDRANT_URL not found in environment variables.")

    try:
        url_clean = qdrant_url
        client_kwargs = {"timeout": 60}
        if qdrant_api_key:
            client_kwargs["api_key"] = qdrant_api_key
            
        if "qdrant.io" in qdrant_url or "cloud" in qdrant_url:
            url_clean = qdrant_url.replace(":6333", "").replace(":6334", "")
            client_kwargs["port"] = 443
            client_kwargs["prefer_grpc"] = False
            print(f"[Qdrant] Detected cloud URL. Connecting using REST over HTTPS (port 443) to: {url_clean} with timeout=60")
            
        client = QC(url=url_clean, **client_kwargs)
        print("[Qdrant] Qdrant client initialized successfully.")
    except Exception as e:
        print(f"[Qdrant ERROR] Failed to connect to Qdrant: {e}")
        raise e

    # Create collection and setup payload index if not existing
    try:
        if not client.collection_exists(collection_name=COLLECTION_NAME):
            model_embedding_dimension = local_embedder.get_sentence_embedding_dimension()
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=model_embedding_dimension,
                    distance=models.Distance.COSINE,
                ),
            )
            print(f"[Qdrant] Collection '{COLLECTION_NAME}' created successfully.")

            # Create payload index for keyword filtering
            for field in ["class_name", "subject", "chapter", "chapter_name", "book_uuid", "chpstpage", "chpendpage"]:
                client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            print("[Qdrant] Payload indexes created successfully.")
        else:
            print(f"[Qdrant] Collection '{COLLECTION_NAME}' already exists. Preserving existing data.")
    except Exception as e:
        print(f"[Qdrant] Warning: Could not verify/create collection at startup (will retry on first request): {e}")
        # Do NOT re-raise — a transient network timeout must not crash the server process.


def get_or_build_bm25_index(book_uuid: str) -> Optional[BM25Okapi]:
    """
    Get cached BM25 index or build one from Qdrant chunks for this book.
    Saves and loads from local disk to prevent 70-second retrieval latency.
    """
    global bm25_indices, book_corpus
    if book_uuid in bm25_indices:
        return bm25_indices[book_uuid]

    # Generate a cache file name based on book_uuid hash
    hashed_name = hashlib.sha256(book_uuid.encode("utf-8")).hexdigest()
    cache_dir = "bm25_indices"
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{hashed_name}.pkl")

    # Try loading from local file cache first
    if os.path.exists(cache_path):
        try:
            print(f"[BM25] Loading cached index from disk: {cache_path}")
            with open(cache_path, "rb") as f:
                cached_data = pickle.load(f)
            if isinstance(cached_data, dict) and "bm25" in cached_data and "corpus_docs" in cached_data:
                bm25 = cached_data["bm25"]
                corpus_docs = cached_data["corpus_docs"]
                
                # Cache in memory
                bm25_indices[book_uuid] = bm25
                book_corpus[book_uuid] = corpus_docs
                print(f"[BM25] Loaded index for book {book_uuid} from disk ({len(corpus_docs)} chunks).")
                return bm25
            else:
                # If it's a legacy pickle file format (contains only BM25 index directly), we must rebuild.
                print(f"[BM25] Cache file {cache_path} is in legacy format. Rebuilding...")
        except Exception as e:
            print(f"[BM25] Error loading cache from disk: {e}")

    # Build it fresh
    print(f"[BM25] Fetching chunks from Qdrant to build index for book {book_uuid}...")
    corpus_docs = _get_all_chunks_for_book(book_uuid)
    if not corpus_docs:
        print(f"[BM25] Warning: No chunks found in Qdrant for book {book_uuid} to build index.")
        return None

    # Tokenize corpus for BM25
    tokenized_corpus = [doc.get("text", "").split(" ") for doc in corpus_docs]
    bm25 = BM25Okapi(tokenized_corpus)

    # Cache both index and corpus in memory
    bm25_indices[book_uuid] = bm25
    book_corpus[book_uuid] = corpus_docs
    print(f"[BM25] Built and cached BM25 index for book {book_uuid} ({len(corpus_docs)} chunks).")

    # Save to disk cache for next time
    try:
        cache_data = {
            "bm25": bm25,
            "corpus_docs": corpus_docs
        }
        with open(cache_path, "wb") as f:
            pickle.dump(cache_data, f)
        print(f"[BM25] Saved index and corpus to disk cache: {cache_path}")
    except Exception as e:
        print(f"[BM25] Warning: Failed to save cache to disk: {e}")

    return bm25


def _get_all_chunks_for_book(book_uuid: str) -> List[Dict]:
    """
    Helper to fetch ALL payload chunks for a given book_uuid from Qdrant.
    Uses scroll API to handle pagination.
    """
    if not client:
        raise RuntimeError("Qdrant client not initialized.")

    all_docs = []
    offset = None

    while True:
        response, next_offset = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))]
            ),
            limit=100,
            with_payload=True,
            offset=offset,
        )

        for point in response:
            all_docs.append(point.payload)

        if not next_offset:
            break
        offset = next_offset

    return all_docs


def process_and_embed_book(pdf_path: str, book_uuid: str, class_name: str, subject: str, chapters: List[Dict]) -> bool:
    """
    Process PDF: Parse pages, split into chunks, map chunks to correct chapters,
    generate local embeddings, and upload to Qdrant.
    """
    if not client or not local_embedder:
        raise RuntimeError("Qdrant client or local embedder not initialized.")

    print(f"\n[INGESTION] Start embedding book UUID: {book_uuid}")
    print(f"[INGESTION] File: {pdf_path}")
    print(f"[INGESTION] Class: {class_name}, Subject: {subject}")
    print(f"[INGESTION] Chapters mapped: {len(chapters)}")

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    print(f"[INGESTION] Total pages in PDF: {total_pages}")

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    points = []
    point_count = 0

    # Process page-by-page
    for page_idx in range(total_pages):
        page_text = reader.pages[page_idx].extract_text()
        if not page_text or not page_text.strip():
            continue

        real_page_num = page_idx + 1  # 1-indexed

        # Find matching chapter
        chapter_name = "General"
        chpstpage = 0
        chpendpage = 0
        pdf_startpg = 0
        pdf_endpg = 0

        for chapter in chapters:
            start = chapter.get("pdf_startpg")
            end = chapter.get("pdf_endpg")
            if start is not None and end is not None:
                if start <= real_page_num <= end:
                    chapter_name = chapter.get("chapter_name", "General")
                    chpstpage = chapter.get("chpstpage", start)
                    chpendpage = chapter.get("chpendpage", end)
                    pdf_startpg = start
                    pdf_endpg = end
                    break

        # Split text into chunks
        chunks = text_splitter.split_text(page_text)
        for chunk_idx, chunk_text in enumerate(chunks):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                continue

            # Generate embedding vector
            embedding = local_embedder.encode(chunk_text).tolist()

            # Create structured payload
            payload = {
                "text": chunk_text,
                "book_uuid": book_uuid,
                "class_name": class_name,
                "subject": subject,
                "chapter": chapter_name,
                "chapter_name": chapter_name,  # Duplicate for route consistency
                "pdf_page": real_page_num,
                "pdf_startpg": pdf_startpg,
                "pdf_endpg": pdf_endpg,
                "chpstpage": chpstpage,
                "chpendpage": chpendpage,
                "chunk_index": chunk_idx,
                "ingested_at": datetime.datetime.utcnow().isoformat(),
            }

            # Generate unique point UUID
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{book_uuid}_{real_page_num}_{chunk_idx}_{hashlib.md5(chunk_text.encode()).hexdigest()}"))

            # Create Qdrant point object
            points.append(models.PointStruct(id=point_id, vector=embedding, payload=payload))
            point_count += 1

    # Upload to Qdrant in batches
    batch_size = 50
    print(f"[INGESTION] Generated {point_count} chunks. Uploading to Qdrant...")
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        client.upsert(collection_name=COLLECTION_NAME, points=batch)

    # Invalidate cached BM25 index
    global bm25_indices
    if book_uuid in bm25_indices:
        del bm25_indices[book_uuid]
    if book_uuid in book_corpus:
        del book_corpus[book_uuid]

    print(f"[INGESTION] Successfully uploaded {point_count} chunks to Qdrant.\n")
    return True


_content_cache: Dict[str, bool] = {}


def book_has_content(book_uuid: str) -> bool:
    """
    Cheap existence check: does this book_uuid have any ingested chunks in
    Qdrant? Firestore can have a classes/{grade}/subjects/{subject} doc with
    chapter metadata/summaries but zero matching Qdrant vectors (metadata
    written, content never (re-)ingested through the parent-child pipeline).
    Callers that need real RAG grounding should gate on this rather than
    trusting Firestore metadata alone. Result is cached in-memory per
    book_uuid since ingestion state doesn't change within a process lifetime.
    """
    if not book_uuid:
        return False
    if book_uuid in _content_cache:
        return _content_cache[book_uuid]
    if not client:
        return False
    try:
        result = client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))]
            ),
            exact=False,
        )
        has_content = result.count > 0
    except Exception as e:
        logger.warning(f"[book_has_content] Count check failed for book_uuid={book_uuid}: {e}")
        has_content = False
    _content_cache[book_uuid] = has_content
    return has_content


def get_chapter_names(book_uuid: str) -> List[str]:
    """
    Get all unique chapter names for a book from Qdrant payload.
    """
    if not client:
        raise RuntimeError("Qdrant client not initialized.")

    response, _ = client.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))]
        ),
        limit=1000,
        with_payload=["chapter"],
    )

    unique_names = set()
    for point in response:
        name = point.payload.get("chapter")
        if name:
            unique_names.add(name)

    return sorted(list(unique_names))


def get_chapters_for_book(book_uuid: str) -> List[Dict]:
    """
    For each chapter name return a dict with name and page ranges.
    Uses payload keys that process_and_embed_book writes: pdf_startpg/pdf_endpg, chap_startpg/chap_endpg
    """
    if not client:
        raise RuntimeError("Qdrant client not initialized.")

    chapter_names = get_chapter_names(book_uuid)
    if not chapter_names:
        return []

    chapter_info = []
    for name in chapter_names:
        response, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid)),
                    models.FieldCondition(key="chapter", match=models.MatchValue(value=name)),
                ]
            ),
            limit=1,
            with_payload=["pdf_startpg", "pdf_endpg", "chpstpage", "chpendpage"], # Fetch these
        )

        pdf_start_page = pdf_end_page = None
        chp_start_page = chp_end_page = None
        if response:
            payload = response[0].payload
            pdf_start_page = payload.get("pdf_startpg")
            pdf_end_page = payload.get("pdf_endpg")
            chp_start_page = payload.get("chpstpage") # Get chpstpage
            chp_end_page = payload.get("chpendpage")   # Get chpendpage

        chapter_info.append(
            {
                "chapter_name": name, # Renamed 'name' to 'chapter_name' for consistency with frontend
                "pdf_startpg": pdf_start_page,
                "pdf_endpg": pdf_end_page,
                "chpstpage": chp_start_page, # Add chpstpage
                "chpendpage": chp_end_page,   # Add chpendpage
            }
        )

    # The sort key is based on 'chpstpage'
    chapter_info.sort(key=lambda x: (x.get("chpstpage") or 0))
    return chapter_info


def hybrid_search(book_uuid: str, query: str, keywords: List[Dict], conceptual_score: float, metadata_filters: Optional[Dict] = None):
    """
    Perform hybrid search: semantic (Qdrant) + BM25 keyword, return top results.
    Returns (ranked_list[:10], semantic_results, normalized_bm25_results).
    """
    if not local_embedder:
        raise RuntimeError("Local embedder not initialized.")

    alpha = 0.4 + (conceptual_score * 0.2)
    
    # Robustly handle keywords that might be dictionaries or strings
    keyword_list = []
    for item in keywords:
        if isinstance(item, dict):
            kw = item.get("keyword")
            if isinstance(kw, dict): # Handle nested dict case
                kw = kw.get("keyword")
            if kw:
                keyword_list.append(str(kw))
        elif isinstance(item, str):
            keyword_list.append(item)
            
    keyword_query_str = " ".join(keyword_list)
    if not keyword_query_str.strip():
        keyword_query_str = query

    # Semantic search
    must_conditions = [models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))]
    
    # Add chapter filter if chapter_names provided (for ranking-based filtering)
    if metadata_filters and "chapter_names" in metadata_filters:
        chapter_names = metadata_filters["chapter_names"]
        if chapter_names:  # Only add if list is not empty
            must_conditions.append(
                models.FieldCondition(
                    key="chapter_name",
                    match=models.MatchAny(any=chapter_names)
                )
            )
            print(f"[HYBRID_SEARCH] Filtering to top {len(chapter_names)} chapters: {', '.join(chapter_names[:3])}...")
    
    # Add other metadata filters
    if metadata_filters:
        for key, value in metadata_filters.items():
            if key != "chapter_names":  # Skip chapter_names as it's already handled
                must_conditions.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))

    query_embedding = local_embedder.encode(query).tolist()
    semantic_results = []
    try:
        query_response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_embedding,
            query_filter=models.Filter(must=must_conditions),
            limit=10,
            with_payload=True,
        )
        semantic_results = query_response.points
    except Exception:
        semantic_results = []

    # BM25 keyword search
    bm25 = get_or_build_bm25_index(book_uuid)
    normalized_bm25_results = []
    top_10_sparse = []
    if bm25:
        corpus_docs = book_corpus.get(book_uuid, [])
        tokenized_query = [w for w in keyword_query_str.split() if w]
        bm25_scores = bm25.get_scores(tokenized_query)

        sparse_results_with_scores = []
        for i, doc in enumerate(corpus_docs):
            if metadata_filters:
                if "chapter" in metadata_filters and doc.get("chapter") != metadata_filters["chapter"]:
                    continue
                if "chapter_names" in metadata_filters:
                    ch_names = metadata_filters["chapter_names"]
                    doc_chapter = doc.get("chapter") or doc.get("chapter_name")
                    if ch_names and doc_chapter not in ch_names:
                        continue
            sparse_results_with_scores.append((bm25_scores[i], doc))

        sparse_results_with_scores.sort(key=lambda x: x[0], reverse=True)
        top_10_sparse = [res for res in sparse_results_with_scores if res[0] > 0][:10]

        if top_10_sparse:
            scores = [score for score, _doc in top_10_sparse]
            min_s, max_s = min(scores), max(scores)
            for score, doc in top_10_sparse:
                norm_score = (score - min_s) / (max_s - min_s) if max_s > min_s else 1.0
                normalized_bm25_results.append((norm_score, doc))

    # Combine results and compute hybrid score
    hybrid_candidates: Dict[str, Dict] = {}
    for res in semantic_results:
        doc_text = res.payload.get("text", "").strip()
        if doc_text not in hybrid_candidates:
            hybrid_candidates[doc_text] = {"semantic": 0, "bm25": 0, "doc": res.payload}
        hybrid_candidates[doc_text]["semantic"] = res.score

    for score, doc in normalized_bm25_results:
        doc_text = doc.get("text", "").strip()
        if doc_text not in hybrid_candidates:
            hybrid_candidates[doc_text] = {"semantic": 0, "bm25": 0, "doc": doc}
        hybrid_candidates[doc_text]["bm25"] = score

    # Combine via Reciprocal Rank Fusion (RRF) instead of raw-score min-max
    # normalization. The old approach rescaled BM25 scores by
    # (score-min)/(max-min) within the top-10 BM25 candidates for this
    # query, which ALWAYS forces the single highest-BM25 candidate to a
    # perfect 1.0 - even when every BM25 candidate is a weak, largely
    # coincidental keyword match. Confirmed live: for "explain Ohm's law
    # clearly with a diagram", an unrelated Myopia passage had the highest
    # raw BM25 score (11.4, barely above a tight cluster of other weak
    # matches 9.5-11.2) purely from generic word overlap ("explain",
    # "clearly", "diagram") - normalizing it to 1.0 let it outrank the
    # genuinely relevant, semantically strong Ohm's law passage in the final
    # hybrid score (0.5 vs 0.288), causing the video-lesson LLM to generate
    # an entire lesson about the wrong topic. RRF combines RANK POSITION
    # instead of raw magnitude, so being #1 among a cluster of weak BM25
    # matches only contributes a small, bounded amount (1/(RRF_K+1)) rather
    # than a full 1.0 weighted equally with semantic relevance - it can no
    # longer single-handedly dominate a chunk with strong semantic
    # relevance but a merely-average BM25 rank.
    RRF_K = 60
    semantic_ranks: Dict[str, int] = {}
    for rank, res in enumerate(semantic_results, start=1):
        doc_text = res.payload.get("text", "").strip()
        if doc_text and doc_text not in semantic_ranks:
            semantic_ranks[doc_text] = rank

    bm25_ranks: Dict[str, int] = {}
    for rank, (_score, doc) in enumerate(top_10_sparse, start=1):
        doc_text = doc.get("text", "").strip()
        if doc_text and doc_text not in bm25_ranks:
            bm25_ranks[doc_text] = rank

    ranked_list = []
    for doc_text, scores in hybrid_candidates.items():
        semantic_rrf = 1.0 / (RRF_K + semantic_ranks[doc_text]) if doc_text in semantic_ranks else 0.0
        bm25_rrf = 1.0 / (RRF_K + bm25_ranks[doc_text]) if doc_text in bm25_ranks else 0.0
        hybrid_score = alpha * semantic_rrf + (1 - alpha) * bm25_rrf
        ranked_list.append((hybrid_score, scores["doc"]))

    ranked_list.sort(key=lambda x: x[0], reverse=True)

    # In-memory parent deduplication
    deduplicated_list = []
    seen_parents = set()
    for score, doc in ranked_list:
        parent_text = doc.get("parent_text", doc.get("text", "")).strip()
        if not parent_text:
            continue
        
        if parent_text not in seen_parents:
            seen_parents.add(parent_text)
            new_doc = dict(doc)
            new_doc["text"] = parent_text
            deduplicated_list.append((score, new_doc))

    return deduplicated_list[:10], semantic_results, normalized_bm25_results


def embed_query(query: str):
    """
    Encode a query string into an embedding vector using the local embedder.
    """
    if not local_embedder:
        raise RuntimeError("Local embedder not initialized.")
    return local_embedder.encode(query).tolist()


# ============================================================================
# Per-student episodic memory (personalized_learning.md SS6.4) and the
# semantic global-cache index (SS6.7). Both are separate Qdrant collections
# from COLLECTION_NAME ("textbooks_v2", the shared textbook content) and from
# each other - the per-student collection is scoped to one uid via a payload
# filter, the global-cache index has no uid at all, matching SS6.5's rule
# that these two layers must never be merged.
# ============================================================================
STUDENT_HISTORY_COLLECTION = os.environ.get("STUDENT_HISTORY_COLLECTION_NAME", "student_history")
GLOBAL_CACHE_INDEX_COLLECTION = os.environ.get("GLOBAL_CACHE_INDEX_COLLECTION_NAME", "global_answer_cache_index")

# Cosine-similarity thresholds. Deliberately different: a per-student memory
# miss just means "answer fresh" (low cost), but a global-cache false
# positive means REPLAYING A POSSIBLY WRONG CACHED ANSWER to a student whose
# question only superficially resembles the cached one (high cost) - so the
# cache-reuse bar is set much stricter than the memory-retrieval bar.
# Calibrated 2026-08-08 against real text-embedding-3-small scores on a live
# digestive/circulatory-system example: genuinely related-but-differently-
# worded questions scored 0.40-0.49, an unrelated control topic scored
# 0.10-0.13 - 0.30 sits with margin on both sides of that real gap.
STUDENT_HISTORY_MIN_SCORE = 0.30
# Calibrated 2026-08-08: a real paraphrase ("explain photosynthesis" vs "how
# does photosynthesis work") scored 0.68-0.73; an unrelated topic scored
# 0.23. 0.62 sits just under the real paraphrase range with margin above the
# unrelated score - deliberately still the stricter of the two thresholds
# (see the module docstring above for why).
GLOBAL_CACHE_MIN_SCORE = 0.62


def _ensure_collection(name: str):
    """Create a Qdrant collection sized to the active embedder if it doesn't exist yet."""
    if client is None or local_embedder is None:
        return
    try:
        if not client.collection_exists(collection_name=name):
            dim = local_embedder.get_sentence_embedding_dimension()
            client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )
            for field in ["uid", "class_name", "subject"]:
                try:
                    client.create_payload_index(
                        collection_name=name, field_name=field,
                        field_schema=models.PayloadSchemaType.KEYWORD,
                    )
                except Exception:
                    pass
            print(f"[Qdrant] Collection '{name}' created successfully.")
    except Exception as e:
        print(f"[Qdrant] Warning: could not ensure collection '{name}': {e}")


def _encode(text: str):
    vector = local_embedder.encode(text)
    return vector.tolist() if hasattr(vector, "tolist") else list(vector)


def text_similarity(text_a: str, text_b: str) -> float:
    """
    Cosine similarity between two raw strings, computed in-memory (no Qdrant
    round-trip) - used by the repeat-question escalation topic check (SS6.3)
    to tell "same topic, asked again" apart from "coincidentally also a
    basic-phrased question, but a different topic entirely."
    """
    if not text_a or not text_b or local_embedder is None:
        return 0.0
    try:
        va = np.array(_encode(text_a), dtype=np.float32)
        vb = np.array(_encode(text_b), dtype=np.float32)
        denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
        if denom == 0.0:
            return 0.0
        return float(np.dot(va, vb) / denom)
    except Exception as e:
        print(f"[TextSimilarity] Failed to compute similarity: {e}")
        return 0.0


def store_student_turn(uid: str, question: str, reformulated_question: str,
                        answer_summary: str, class_name, subject: str,
                        topic: str = None) -> None:
    """
    Persist one student's Q&A turn as a searchable memory point (SS6.4).
    Called once per answered turn, alongside (not instead of) the global
    cache write - see chat.py.
    """
    if client is None or local_embedder is None or not uid or uid == "anonymous":
        return
    try:
        _ensure_collection(STUDENT_HISTORY_COLLECTION)
        embed_text = f"{reformulated_question or question}\n{(answer_summary or '')[:500]}"
        vector = _encode(embed_text)
        point_id = str(uuid.uuid4())
        client.upsert(
            collection_name=STUDENT_HISTORY_COLLECTION,
            points=[models.PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "uid": uid,
                    "question": question,
                    "reformulated_question": reformulated_question,
                    "answer_summary": (answer_summary or "")[:1500],
                    "class_name": str(class_name),
                    "subject": str(subject or "").lower(),
                    "topic": topic or "",
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                },
            )],
        )
    except Exception as e:
        print(f"[StudentHistory] Failed to store turn for uid={uid}: {e}")


def retrieve_student_history(uid: str, query: str, limit: int = 3) -> List[Dict]:
    """
    Semantic lookup of this student's OWN past turns related to `query`
    (SS6.4) - the direct fix for the "blunder answer on follow-up" bug
    (personalized_learning.md SS2.4). Empty list on a genuinely new topic,
    on the student's first-ever question, or on any retrieval error.
    """
    if client is None or local_embedder is None or not uid or uid == "anonymous":
        return []
    try:
        _ensure_collection(STUDENT_HISTORY_COLLECTION)
        vector = _encode(query)
        results = client.search(
            collection_name=STUDENT_HISTORY_COLLECTION,
            query_vector=vector,
            query_filter=models.Filter(
                must=[models.FieldCondition(key="uid", match=models.MatchValue(value=uid))]
            ),
            limit=limit,
        )
        hits = [r for r in results if r.score >= STUDENT_HISTORY_MIN_SCORE]
        return [
            {
                "question": h.payload.get("question"),
                "reformulated_question": h.payload.get("reformulated_question"),
                "answer_summary": h.payload.get("answer_summary"),
                "topic": h.payload.get("topic"),
                "score": h.score,
                "timestamp": h.payload.get("timestamp"),
            }
            for h in hits
        ]
    except Exception as e:
        print(f"[StudentHistory] Failed to retrieve history for uid={uid}: {e}")
        return []


def index_global_cache_entry(doc_id: str, raw_query: str, class_name: str, subject: str) -> None:
    """
    SS6.7: index a freshly-cached answer's question text semantically, so a
    later, differently-worded-but-same-intent question can still find it.
    The Firestore doc (written by save_to_global_query_cache) stays the
    source of truth for the answer payload - this collection only stores
    enough to find that doc's ID again.
    """
    if client is None or local_embedder is None or not raw_query:
        return
    try:
        _ensure_collection(GLOBAL_CACHE_INDEX_COLLECTION)
        vector = _encode(raw_query)
        point_id = str(uuid.uuid4())
        client.upsert(
            collection_name=GLOBAL_CACHE_INDEX_COLLECTION,
            points=[models.PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "doc_id": doc_id,
                    "raw_query": raw_query,
                    "class_name": str(class_name),
                    "subject": str(subject or "").lower(),
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                },
            )],
        )
    except Exception as e:
        print(f"[GlobalCacheIndex] Failed to index doc_id={doc_id}: {e}")


def find_semantic_cache_match(query: str, class_name: str, subject: str) -> Optional[str]:
    """
    SS6.7: given a query that missed the exact-text cache check, look for a
    semantically-equivalent cached question and return its Firestore doc_id
    (or None). Uses a deliberately high similarity bar (GLOBAL_CACHE_MIN_SCORE)
    since a false positive here means replaying a possibly-wrong cached
    answer to an unrelated question.
    """
    if client is None or local_embedder is None or not query:
        return None
    try:
        _ensure_collection(GLOBAL_CACHE_INDEX_COLLECTION)
        vector = _encode(query)
        must = [models.FieldCondition(key="class_name", match=models.MatchValue(value=str(class_name)))]
        subj_str = str(subject or "").strip().lower()
        if subj_str and subj_str not in ["all", "none", "choose your subject..."]:
            must.append(models.FieldCondition(key="subject", match=models.MatchValue(value=subj_str)))
        results = client.search(
            collection_name=GLOBAL_CACHE_INDEX_COLLECTION,
            query_vector=vector,
            query_filter=models.Filter(must=must),
            limit=1,
        )
        if results and results[0].score >= GLOBAL_CACHE_MIN_SCORE:
            return results[0].payload.get("doc_id")
        return None
    except Exception as e:
        print(f"[GlobalCacheIndex] Semantic lookup failed: {e}")
        return None


def clear_qdrant_collection():
    """
    Deletes and re-creates the Qdrant collection, effectively clearing all data.
    """
    if not client:
        raise RuntimeError("Qdrant client not initialized.")
    
    try:
        # Delete the collection if it exists
        if client.collection_exists(collection_name=COLLECTION_NAME):
            client.delete_collection(collection_name=COLLECTION_NAME)
        
        # Re-create the collection
        model_embedding_dimension = local_embedder.get_sentence_embedding_dimension()
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=model_embedding_dimension,
                distance=models.Distance.COSINE,
            ),
        )
        
        # Re-create payload indexes
        for field in ["class_name", "subject", "chapter", "book_uuid", "chpstpage", "chpendpage"]:
            try:
                client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            except Exception as e:
                pass  # Silently fail if index already exists
        
        print(f"[Qdrant] Collection '{COLLECTION_NAME}' cleared and re-initialized successfully.")
    except Exception as e:
        print(f"[Qdrant] Error clearing collection: {e}")
        raise

# Helper to fetch metadata (to avoid circular dependency with app)
def get_book_metadata(book_uuid: str) -> Dict:
    """
    Get a single chunk from Qdrant to read the book's class and subject metadata.
    """
    if not client:
        return {}
    try:
        response, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="book_uuid", match=models.MatchValue(value=book_uuid))]
            ),
            limit=1,
            with_payload=["class_name", "subject"],
        )
        if response:
            return response[0].payload
    except:
        pass
    return {}
