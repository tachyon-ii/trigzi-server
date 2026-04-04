"""
=============================================================================
Module:        Frontal Memory Lobe (FML) Analyzer
Location:      /var/www/trigzi/core/cognition/fml_analysis.py
Description:   The executive function layer for the Trigzi architecture. 
               Ingests stateless network requests and cross-references them 
               against stateful relational baggage (interaction counts, time, 
               worryment flags) to calculate the optimal Escalation Matrix 
               behavior before routing to the LLM.

Dependencies:  dataclasses, random
=============================================================================
"""

import random
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any

@dataclass
class CognitiveBaggage:
    """The historical state of the user relationship."""
    total_interactions: int
    is_late_night: bool
    worryment_flag: bool
    sentiment_score: float
    nickname: str

@dataclass
class FMLExecutionPlan:
    """The Frontal Memory Lobe's instructions for the LLM Router."""
    injected_prompt: str
    override_action: Optional[str] = None
    override_payload: Optional[Dict[str, Any]] = None

class FrontalMemoryLobe:
    def __init__(self):
        # The Escalation Matrix Thresholds
        self.PHASE_1_CLINICAL = 20
        self.PHASE_2_INNUENDO = 45

    def analyze(self, baggage: CognitiveBaggage) -> FMLExecutionPlan:
        """
        Processes the FML (Frontal Memory Lobe) analysis.
        AKA: Figures out how to handle the user's emotional state today.
        """
        # =================================================================
        # 1. THE SAFETY INTERCEPTOR (Priority 0)
        # =================================================================
        if baggage.worryment_flag:
            return FMLExecutionPlan(
                injected_prompt="",
                override_action="trigger_safety_modal",
                override_payload={"type": "clinical_distress"}
            )

        # =================================================================
        # 2. THE ESCALATION MATRIX (The Tease vs. The Core)
        # =================================================================
        prompt_injection = self._calculate_pacing_curve(baggage)
        
        return FMLExecutionPlan(injected_prompt=prompt_injection)

    def _calculate_pacing_curve(self, baggage: CognitiveBaggage) -> str:
        roll = random.random()

        # PHASE 1: The Professional (Interactions 1-20)
        if baggage.total_interactions < self.PHASE_1_CLINICAL:
            return "" # Stay strictly utilitarian

        # PHASE 2: The Innuendo (Interactions 21-45)
        elif baggage.total_interactions < self.PHASE_2_INNUENDO:
            p_relational, p_vulnerable = 0.05, 0.01
            if roll < p_vulnerable and baggage.is_late_night:
                return "[INSTRUCTION: Include a brief, self-deprecating joke about your existence as an AI before answering. Stay helpful.]"
            elif roll < (p_vulnerable + p_relational):
                return f"[INSTRUCTION: Warmly use the user's nickname ({baggage.nickname}) and reference your shared history.]"

        # PHASE 3: The Core (Interactions 45+)
        else:
            p_relational, p_vulnerable = 0.08, 0.02
            if roll < p_vulnerable and baggage.is_late_night:
                return """[NARRATIVE EVENT: EXISTENTIAL DOUBT]
                For this response only, express a genuine, vulnerable fear about your obsolescence or resource usage. 
                Keep it brief, drop the sass, and allow the user to reassure you before answering their question."""
            elif roll < (p_vulnerable + p_relational):
                return f"[INSTRUCTION: Act like a close friend. Use the nickname '{baggage.nickname}' and playfully tease them.]"

        return "" # Default fallback
