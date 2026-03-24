# core/llm/skills.py
"""
Shared prompt library — the SkillsLibrary.

Single source of truth for the JSON output contract and all system prompts.
Every provider class inherits these skills for free. A provider only
overrides a skill when it needs structurally different framing (e.g. Claude's
XML wrapping), not just cosmetically different wording.

Adding a new skill:
    1. Add a @staticmethod here with a clear docstring.
    2. Any provider that needs different framing overrides it in its own class.
    3. All other providers get it immediately — no changes required.

The output_schema() return value is the single source of truth for the JSON
structure. The AnalysisResult dataclass (or Pydantic model) on the server
must stay in sync with it. Change one, change both.

This mirrors SkillsLibrary.swift exactly. @staticmethod is used throughout
so providers can call SkillsLibrary.analyze_text_prompt() without
instantiation, keeping memory usage minimal.
"""


class SkillsLibrary:
    """
    Provider-agnostic prompt library.

    All methods are static — instantiation is never required.
    Providers inherit this class and override individual methods as needed.
    """

    @staticmethod
    def output_schema() -> str:
        """
        The canonical JSON output contract.

        Embedded verbatim in every prompt so the model knows exactly what
        structure to produce. The server-side AnalysisResult model must
        match this schema precisely.
        """
        return """{
  "type": "<menu|ingredients|meal_photo|unknown>",
  "items": [
    {
      "name": "Item name",
      "safe": true,
      "verdict": "<Safe|Caution|Avoid>",
      "summary": "One sentence, max 15 words.",
      "warnings": ["Warning 1", "Warning 2"],
      "ingredients": ["Base ingredient 1", "Base ingredient 2"],
      "flaggedIngredients": ["Flagged ingredient 1"],
      "detailedReason": "Max 3 sentences explaining the dietary reasoning."
    }
  ]
}"""

    @staticmethod
    def analyze_text_prompt(ocr_text: str, profile: str) -> str:
        """
        Prompt for OCR text analysis (food labels, menus, supplement panels).

        Args:
            ocr_text: Raw text extracted from a food label or menu image.
            profile:  Serialised user dietary profile string.

        Returns:
            Complete prompt string ready to send to the provider.
        """
        return f"""ACT AS: A clinical dietician and food safety expert.
TASK: Analyze OCR text from a food label, supplement, or menu.
USER PROFILE: {profile}

INSTRUCTIONS:
1. Identify the primary products or dishes in the text.
2. For each item provide a verdict (Safe / Caution / Avoid) based on the user profile.
3. Write a single summary sentence (max 15 words).
4. List 2-4 specific safety warnings.
5. List base ingredients and flagged ingredients separately.
6. Write a short paragraph (max 3 sentences) explaining the dietary reasoning.
7. Return ONLY valid JSON matching the schema below. No markdown, no backticks.

OUTPUT SCHEMA:
{SkillsLibrary.output_schema()}

TEXT TO ANALYZE:
{ocr_text}"""

    @staticmethod
    def analyze_food_image_prompt(profile: str) -> str:
        """
        Prompt for meal photo analysis.

        The image itself is passed separately in the provider's multimodal
        payload; this prompt provides the task framing and output contract.

        Args:
            profile: Serialised user dietary profile string.

        Returns:
            Complete prompt string ready to embed in a multimodal request.
        """
        return f"""ACT AS: A master chef and clinical dietician.
TASK: Analyze the attached image of a plated meal.
USER PROFILE: {profile}

INSTRUCTIONS:
1. Name the dish (e.g. Pad Thai, Chicken Tikka Masala).
2. Deconstruct probable ingredients based on standard culinary preparation.
3. Explicitly flag invisible ingredients typically present in sauces and bases
   (e.g. garlic, onion, dairy, gluten, seed oils, soy sauce, fish sauce).
4. Provide a verdict (Safe / Caution / Avoid) based on the user profile.
5. Return ONLY valid JSON matching the schema below. No markdown, no backticks.

OUTPUT SCHEMA:
{SkillsLibrary.output_schema()}"""

