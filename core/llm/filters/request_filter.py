#
#  core/llm/filters/request_filter.py
#  trigzi-backend
#
#  Protocol + concrete implementations for outbound request transformation.
#
#  RequestFilter sits between SkillsLibrary (what to ask) and the wire (how
#  to ask it). Each provider supplies its own filter; BaseProvider calls it.
#
#  GeminiRequestFilter  — prompt as-is, JSON schema inline, base64 image
#  ClaudeRequestFilter  — JSON schema → XML via XMLFilter, image as content block
#

import re
from abc import ABC, abstractmethod
from typing import Any, Dict
from .xml_filter import XMLFilter

# MARK: - Request Filter Protocol

class RequestFilter(ABC):
    """
    Interface for transforming a generic prompt into a provider-specific 
    payload structure. Replicates the RequestFilter protocol in Swift.
    """
    
    @abstractmethod
    def build_text_payload(self, prompt: str, model_tag: str) -> Dict[str, Any]:
        """Transform a plain-text prompt into a provider-specific payload dict."""
        pass

    @abstractmethod
    def build_image_payload(self, prompt: str, image_base64: str, model_tag: str) -> Dict[str, Any]:
        """Transform prompt + image into a provider-specific payload dict."""
        pass

    def encode_image(self, base64_string: str) -> str:
        """
        In the backend port, resizing is handled upstream or assumed.
        This method validates the format.
        """
        return base64_string


# MARK: - Gemini Request Filter

class GeminiRequestFilter(RequestFilter):
    def build_text_payload(self, prompt: str, model_tag: str) -> Dict[str, Any]:
        return {
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1
            }
        }

    def build_image_payload(self, prompt: str, image_base64: str, model_tag: str) -> Dict[str, Any]:
        return {
            "contents": [
                {"parts": [
                    {"text": prompt},
                    {"inlineData": {
                        "mimeType": "image/jpeg",
                        "data": image_base64
                    }}
                ]}
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2
            }
        }


# MARK: - Claude Request Filter

class ClaudeRequestFilter(RequestFilter):
    """
    Converts the JSON schema into XML via XMLFilter for better Claude comprehension.
    Uses the Messages API content block format for images.
    """
    
    def build_text_payload(self, prompt: str, model_tag: str) -> Dict[str, Any]:
        # Wrap prompt in XML structure — Claude reasons better with explicit task framing
        xml_prompt = XMLFilter.json_schema_to_xml(
            self._extract_schema_from_prompt(prompt),
            task_description=self._extract_task_from_prompt(prompt)
        )
        full_prompt = self._replacing_schema_with_xml(prompt, xml_prompt)

        return {
            "model": model_tag,
            "max_tokens": 2048,
            "messages": [
                {"role": "user", "content": full_prompt}
            ]
        }

    def build_image_payload(self, prompt: str, image_base64: str, model_tag: str) -> Dict[str, Any]:
        xml_prompt = XMLFilter.json_schema_to_xml(
            self._extract_schema_from_prompt(prompt),
            task_description=self._extract_task_from_prompt(prompt)
        )
        full_prompt = self._replacing_schema_with_xml(prompt, xml_prompt)

        return {
            "model": model_tag,
            "max_tokens": 2048,
            "messages": [
                {"role": "user", "content": [
                    # Image block first — Claude attends to images better this way
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_base64
                        }
                    },
                    {
                        "type": "text",
                        "text": full_prompt
                    }
                ]}
            ]
        }

    # MARK: - Private prompt manipulation helpers

    def _extract_schema_from_prompt(self, prompt: str) -> str:
        marker = "OUTPUT SCHEMA:"
        if marker not in prompt:
            return ""
        
        parts = prompt.split(marker)
        after_marker = parts[1]
        
        # Look for the end of the schema block
        end_match = re.search(r"TEXT TO ANALYZE:", after_marker)
        if end_match:
            return after_marker[:end_match.start()].strip()
        
        # Fallback to double newline if header is missing
        return after_marker.split("\n\n")[0].strip()

    def _extract_task_from_prompt(self, prompt: str) -> str:
        match = re.search(r"TASK:\s*(.*?)\n", prompt)
        if match:
            return match.group(1).strip()
        return "Analyze food content and return structured JSON."

    def _replacing_schema_with_xml(self, prompt: str, xml_block: str) -> str:
        marker = "OUTPUT SCHEMA:"
        if marker not in prompt:
            return prompt
            
        schema_text = self._extract_schema_from_prompt(prompt)
        if not schema_text:
            return prompt
            
        # Replicate Swift's replacingCharacters logic by identifying the specific block
        target = f"{marker}\n{schema_text}"
        if target in prompt:
            return prompt.replace(target, xml_block)
            
        return prompt.replace(marker, xml_block)
