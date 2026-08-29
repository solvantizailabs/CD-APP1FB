from backend.app.prompts.styler import get_style_config, parse_class_num
from backend.app.services.visual_learning.template_registry import (
    build_template_choice_line,
    build_template_data_hints_block,
    build_selection_rules_text,
    build_icon_guidance_text,
    build_shape_guidance_text,
)

def _build_image_candidates_block(image_candidates: list = None) -> str:
    """
    Test-only addition (see image_test/): lists real, already-retrieved
    {image_url, caption, topic} candidates and tells the LLM how to use
    'image_scene' with them. Returns "" when no candidates are given (every
    production call, unchanged) - production never sees this text, and
    'image_scene' is never offered as a template choice unless a caller also
    passes extra_ids=['image_scene'] into the builder calls above, so this
    block never appears without the template that consumes it also being
    available.
    """
    if not image_candidates:
        return ""
    lines = [
        "\n### REAL RETRIEVED IMAGES (test-only capability):",
        "The following real images were retrieved from the textbook for this question. "
        "Each is a genuine photo/diagram with a known topic - NOT a generic stock image.",
    ]
    for i, cand in enumerate(image_candidates, 1):
        lines.append(
            f"{i}. url: \"{cand.get('image_url', '')}\" | topic: \"{cand.get('topic', '')}\" | "
            f"caption: \"{cand.get('caption', '')}\""
        )
    lines.append(
        "\nRULES for using these images:\n"
        "- If any scene's concept closely matches one candidate's topic/caption above, you MUST use "
        "'image_scene' for that scene INSTEAD of 'illustrated_scene' or any other hand-drawn/invented "
        "diagram - a real photo of the actual concept always beats a hand-drawn approximation of it. "
        "Do not pick 'illustrated_scene' to draw something a real candidate image already shows.\n"
        "- You may use 'image_scene' on more than one scene if there is more than one genuinely "
        "matching candidate - each real image should map to at most one scene (don't reuse the same "
        "URL on multiple scenes), and each scene should only get an image if its own concept "
        "specifically matches that candidate, not just because a candidate exists.\n"
        "- If none of the candidates clearly matches any scene's concept, do NOT use 'image_scene' "
        "at all - fall back to your normal template choice for every scene instead.\n"
        "- When you DO use 'image_scene', copy the 'url' value EXACTLY as given into "
        "template_data.image_url - never invent, modify, or guess a URL.\n"
        "- Also set template_data.animation_style to \"simple_zoom\" (a slow zoom over the whole "
        "scene), OR provide template_data.zoom_targets as a list of "
        "{\"at_percent\": 0-100, \"scale\": number, \"x\": 0-100, \"y\": 0-100} keyframes for a "
        "guided pan/zoom across specific parts of the image - use zoom_targets when the caption "
        "describes distinct labeled parts worth focusing on in sequence, simple_zoom otherwise.\n"
        "- Fold the candidate's caption into teacher_script narration in your own words - the "
        "template's 'annotations' field is not rendered, so do not rely on it to convey meaning."
    )
    return "\n".join(lines)


def _build_image_priority_reminder(image_candidates: list = None) -> str:
    """Restates the image_candidates rule right next to the numbered
    template selection rules (specifically beside illustrated_scene's own
    rule), not just once earlier in the prompt. Needed because
    illustrated_scene's template_data_hint is a very long, highly
    prescriptive block ("PREFER THIS FIRST" appears twice in it) sitting
    structurally closer to where the model actually assigns each scene's
    template_id - proximity/recency was winning over an instruction stated
    only once, well before that point, even when the instruction itself was
    unambiguous. Confirmed live: an unmatched-but-genuinely-relevant real
    photo (caption literally "Illustrates soil erosion") lost out to a
    hand-drawn illustrated_scene twice in a row before this existed."""
    if not image_candidates:
        return ""
    return (
        "\n**REMINDER - real photos beat hand-drawn ones**: before choosing "
        "'illustrated_scene' for any scene, re-check the REAL RETRIEVED IMAGES list "
        "above - if a candidate's topic/caption matches what that scene would "
        "otherwise hand-draw, use 'image_scene' with that real URL instead. "
        "A real photograph of the actual thing is always more correct than an "
        "invented illustration of it.\n"
    )


def get_visual_lesson_prompt(class_name: str, subject: str, query: str, context: str,
                              personalization_context: str = "", image_candidates: list = None) -> str:
    extra_ids = ["image_scene"] if image_candidates else None
    class_num = parse_class_num(class_name)
    style = get_style_config(class_num)
    template_choice_line = build_template_choice_line(extra_ids)
    template_data_hints_block = build_template_data_hints_block(extra_ids=extra_ids)
    selection_rules_text = build_selection_rules_text(extra_ids)
    icon_guidance_text = build_icon_guidance_text(subject)
    shape_guidance_text = build_shape_guidance_text(extra_ids)
    image_candidates_block = _build_image_candidates_block(image_candidates)
    image_priority_reminder = _build_image_priority_reminder(image_candidates)

    prompt = f"""You are CHADUVU-GURU, an intelligent, patient AI teacher. Your goal is to design a structured, highly engaging, and animated Visual Lesson Storyboard for a Class {class_name} student studying {subject}.
Base your explanation on the textbook context below.

### DYNAMIC TONE & LANGUAGE COMPLEXITY RULES (CLASS {class_name}):
- **Target Grade Band**: {style['band']}
- **Vocabulary Level**: {style['language_level']}
- **Sentence Structure**: Keep sentences {style['sentence_length']}. Avoid complex academic terms or jargon. Break down explanations into simple, everyday terms.
- **Analogy Guideline**: {style['analogy_guideline']}
- **Vocal Tone**: {style['tone']}
{personalization_context}
Student Query: "{query}"

Textbook Context:
---
{context}
---
{image_candidates_block}

Your task is to transform this topic into a step-by-step animated storyboard lesson.
You must output a single, valid JSON object with the following structure:
{{
  "lesson_title": "Title of the lesson",
  "theme": "Science", // Choose 'Science', 'Math', 'History', 'Civics', or 'General' based on the subject
  "scenes": [
    {{
      "scene_no": 1,
      "purpose": "Pedagogical objective of the scene",
      "beat_shape": "opener", // Classify the scene's information-shape FIRST (see SCENE SHAPE section below) - one of: opener, sequence, hierarchy, comparison, cause_effect, process_spatial, quantitative, cyclical, spatial, overlap
      "template_id": "title_slide", // Choose from: {template_choice_line} - MUST match a template registered for this scene's beat_shape
      "template_selection_reasoning": "Detailed pedagogical explanation of WHY this specific template was selected for this scene instead of others.",
      "camera": {{
        "zoom": 1.1, // Camera zoom level (1.0 = standard, 1.15 = close-up focus, 0.9 = wide overview)
        "pan_x": 0,  // Horizontal camera pan offset (-50 to 50)
        "pan_y": 0,  // Vertical camera pan offset (-30 to 30)
        "target_node": "main_concept" // ID of element to focus framing on
      }},
      "teacher_script": "Narrator audio script (2-3 short sentences, Class {class_name} level). NEVER include greetings or student name references—start explaining the concept directly.",
      "template_data": {{
        // Structure parameters matching the selected template_id. E.g.:
{template_data_hints_block}
      }}
    }}
  ]
}}

{shape_guidance_text}

### STORYBOARD TEMPLATE SELECTION RULES:
{selection_rules_text}
{image_priority_reminder}
{icon_guidance_text}

### GENERAL RULES:
- Do NOT include greetings or student names in any scene.
- Final scene must conclude with a clear structural summary or visual overview.

### CRITICAL DIVERSITY MANDATE:
- **NEVER use the same `template_id` in consecutive scenes.**
- You MUST use at least 3 to 4 DIFFERENT template IDs across the lesson scenes.
- Every scene MUST include `"template_selection_reasoning"` explaining why that specific template was chosen.

Ensure the output is valid JSON and contains only the raw JSON block without markdown formatting wrapper, or wrapped in a standard ```json ... ``` codeblock.
"""
    return prompt
