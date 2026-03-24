#
#  core/llm/filters/xml_filter.py
#  trigzi-backend
#
#  Standalone JSON ↔ XML converter used by the filter pipeline.
#  Lives outside any provider so it can be reused for audit, replay,
#  or any future context that needs format conversion.
#
#  JSON → XML: converts the AnalysisResult schema into Claude-friendly
#  XML structure with <task>, <schema>, and <thinking> scaffolding.
#
#  XML → JSON: not needed for current flow (providers return JSON)
#  but included for audit replay and future use.
#

import json
import re
from typing import Any, Optional

# MARK: - Usage Guide
#
# Two calling conventions are supported:
#
# 1. Class style (preferred — full feature set):
#
#      from core.llm.filters.xml_filter import XMLFilter
#
#      xml  = XMLFilter.json_schema_to_xml(schema_json, "Analyze food content.")
#      text = XMLFilter.strip_thinking(claude_response)
#      json = XMLFilter.xml_to_json(xml_string)
#
# 2. Function style (legacy shims — backwards compatibility):
#
#      from core.llm.filters.xml_filter import dict_to_xml, wrap_in_tag
#
#      xml  = dict_to_xml({"task": "Analyze", "profile": "Nut Allergy"})
#      text = wrap_in_tag("instructions", prompt)
#
# The class style handles full JSON schema conversion, thinking-block
# stripping, and round-trip XML->JSON for audit replay.
# The function shims are kept for compatibility with filters/__init__.py
# and existing tests — prefer the class in new code.

class XMLFilter:
    """
    Static utility class for JSON/XML format conversion.
    Used primarily to wrap schemas for Anthropic Claude models to encourage reasoning.
    """

    # MARK: - JSON → XML

    @staticmethod
    def json_schema_to_xml(json_str: str, task_description: str) -> str:
        """
        Wraps a JSON schema string in XML structure that Claude responds 
        well to. The <thinking> tag instructs Claude to reason before 
        producing the answer.
        """
        return f"""<task>
{task_description}
</task>

<thinking>
Before producing the JSON output, reason through:
- What ingredients are present or likely present?
- Which ingredients conflict with the user profile?
- What is the overall safety verdict and why?
</thinking>

<output_schema>
{XMLFilter.json_to_xml_elements(json_str)}
</output_schema>

<instructions>
Respond ONLY with valid JSON matching the schema above.
Do not include XML tags, markdown, or backticks in your response.
Strip your thinking — return only the final JSON object.
</instructions>"""

    @staticmethod
    def json_to_xml_elements(json_str: str) -> str:
        """
        Converts a JSON object string into readable XML element descriptions.
        """
        try:
            obj = json.loads(json_str)
            return XMLFilter._serialize_to_xml(obj, indent=0)
        except json.JSONDecodeError:
            return f"<raw>{json_str}</raw>"

    # MARK: - XML → JSON

    @staticmethod
    def xml_to_json(xml: str) -> Optional[str]:
        """
        Extracts the JSON payload from an XML-wrapped response.
        Strips any <thinking>…</thinking> blocks before parsing.
        """
        working = xml
        working = XMLFilter.strip_thinking(working)

        # Handle explicit output tags if present
        inner = XMLFilter._extract_xml_tag("output", working)
        if inner:
            working = inner

        trimmed = working.strip()
        if not (trimmed.startswith("{") or trimmed.startswith("[")):
            return None
        return trimmed

    # MARK: - Thinking block stripper

    @staticmethod
    def strip_thinking(response: str) -> str:
        """Removes <thinking>…</thinking> from raw provider responses."""
        return XMLFilter._strip_xml_tag("thinking", response)

    # MARK: - Private helpers

    @staticmethod
    def _serialize_to_xml(value: Any, indent: int) -> str:
        pad = "  " * indent

        if isinstance(value, dict):
            items = []
            for key in sorted(value.keys()):
                val = value[key]
                inner = XMLFilter._serialize_to_xml(val, indent + 1)
                is_complex = isinstance(val, (list, dict))
                
                if is_complex:
                    items.append(f"{pad}<{key}>\n{inner}\n{pad}</{key}>")
                else:
                    items.append(f"{pad}<{key}>{inner}</{key}>")
            return "\n".join(items)

        elif isinstance(value, list):
            items = []
            for idx, item in enumerate(value):
                inner = XMLFilter._serialize_to_xml(item, indent + 1)
                items.append(f'{pad}<item index="{idx}">\n{inner}\n{pad}</item>')
            return "\n".join(items)

        elif isinstance(value, str):
            return value

        elif value is None:
            return ""

        else:
            return str(value)

    @staticmethod
    def _strip_xml_tag(tag: str, text: str) -> str:
        """Removes all instances of a specific XML tag and its content."""
        pattern = f"<{tag}>.*?</{tag}>"
        return re.sub(pattern, "", text, flags=re.DOTALL).strip()

    @staticmethod
    def _extract_xml_tag(tag: str, text: str) -> Optional[str]:
        """Extracts the content between a specific XML tag pair."""
        pattern = f"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, flags=re.DOTALL)
        if match:
            return match.group(1).strip()
        return None


# MARK: - Module-level shims
# Convenience functions delegating to XMLFilter for backwards compatibility
# with imports in filters/__init__.py and test_filters.py

def dict_to_xml(data: dict) -> str:
    """Delegate to XMLFilter._serialize_to_xml for dict → XML conversion."""
    return XMLFilter._serialize_to_xml(data, indent=0)


def wrap_in_tag(tag: str, content: str) -> str:
    """Wrap content string in an XML tag."""
    return f"<{tag}>{content}</{tag}>"
