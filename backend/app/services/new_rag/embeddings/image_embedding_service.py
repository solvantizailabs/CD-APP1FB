"""
Image embedding generation for diagram visual retrieval (Stage 2 of the
image pipeline - see docs/IMAGE_PIPELINE_PLAN.md section 3).

Locked decision for this build: a LOCAL CLIP model
(sentence-transformers' "clip-ViT-B-32"), not an API-based multimodal
embedder. OpenAI has no public multimodal embedding endpoint; Voyage
multimodal-3 / Cohere Embed 4 were the research-flagged stronger options
for label/formula-heavy diagrams (docs/IMAGE_PIPELINE_PLAN.md section 3.1)
but need API keys/credits this project doesn't currently have configured -
this pipeline's OpenAI account is already at zero credits, blocking
captioning and text-embedding elsewhere. Running CLIP locally means Stage 2
can be built AND actually verified today rather than joining that same
blocked queue. Swapping to a hosted multimodal embedder later only means
swapping this module's two functions - image_indexer.py's collection
schema doesn't change, since it stores plain float vectors either way.

sentence-transformers is already a dependency in this codebase (see
qdrant_service.py's legacy local_embedder), so this introduces no new
package.
"""
import logging
from functools import lru_cache
from typing import List

from PIL import Image

logger = logging.getLogger(__name__)

CLIP_MODEL_NAME = "clip-ViT-B-32"
CLIP_DIM = 512


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer
    logger.info(f"[NEW_RAG][ImageEmbed] Loading local CLIP model {CLIP_MODEL_NAME!r} (first call only, cached after)...")
    return SentenceTransformer(CLIP_MODEL_NAME)


def embed_images(images: List[Image.Image]) -> List[List[float]]:
    """Embeds a batch of already-decoded PIL images into CLIP's 512-dim space."""
    if not images:
        return []
    model = _get_model()
    vectors = model.encode(images, convert_to_numpy=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_text_query(text: str) -> List[float]:
    """
    Embeds a natural-language query into the SAME CLIP space as
    embed_images() - this is what makes cross-modal search possible: a
    query like "diagram of the water cycle" gets compared directly against
    image vectors, not just against caption text. CLIP's text tower is a
    different embedding space than the app's normal text embedder
    (text-embedding-3-small, embedding_service.py) - this vector must only
    ever be compared against embed_images() output, never mixed with
    textbooks_v3's dense vectors.
    """
    model = _get_model()
    vector = model.encode([text], convert_to_numpy=True, show_progress_bar=False)[0]
    return vector.tolist()
