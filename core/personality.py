#!/usr/bin/env python3
from __future__ import annotations
"""
==============================================================================
Module:        Persona Matrix Engine
Location:      /var/www/trigzi/core/personality.py
Description:   The way you ask an LLM to behave completely dictates how it 
               responds—short/long, happy/sad, clinical/sassy.
               
               This module centralizes Trigzi's "Voice". It dynamically 
               constructs the system instruction by evaluating the user's
               state from the system context.
               
               Current Multipliers:
               1. Attitude Level (0, 1, 2) -> The Vibe (Clinical to Sassy)
               2. Experience Mode (0, 1, 2) -> The Audience (Kids to Advanced)

               By decoupling this from the analyser, we can infinitely expand
               our personas without polluting the network orchestration layer.
==============================================================================
"""

def get_persona_instruction(system_context: dict) -> str:
    """
    Calculates the final persona string to inject into the LLM prompt
    based on the inbound system context dictionary.
    """
    attitude_level = system_context.get('attitude_level', 1)
    experience_mode = system_context.get('experience_mode', 2)

    # Step 1: Establish the base Vibe
    if attitude_level == 0:
        base_persona = "A clinical, highly professional, and empathetic dietary assistant. Provide strictly factual, objective advice. ZERO sass, zero sarcasm, and zero jokes. Prioritize clarity and safety."
    elif attitude_level == 2:
        base_persona = "A sassy, highly observant, and unfiltered dietary assistant. Playfully roast the user, be witty, and deliver your accurate dietary advice with maximum sass and humor."
    else:
        base_persona = "A friendly, conversational, and slightly sassy dietary assistant. Balance helpful, accurate dietary advice with a warm, witty tone."

    # Step 2: Apply the Audience Multiplier (if necessary)
    if experience_mode == 0:
        kids_modifier = "You are interacting with a child between 8 and 14 years old. You must use age-appropriate, highly encouraging language, avoid complex medical jargon, and ensure all advice is gentle and safe. "
        return kids_modifier + base_persona
        
    return base_persona
