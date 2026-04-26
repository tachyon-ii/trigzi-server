# pylint: disable=line-too-long
"""
=============================================================================
Module:        MOTD Message Catalogue
Location:      core/messages/motd.py
Description:   Pure data file holding the message-of-the-day quote pool.
               One entry type within the broader messages system. No logic,
               no imports — every entry is consumed by
               core/messages/messages_service.py via the QUOTES list.

Architecture Note:
The line-too-long pylint check is disabled at the module level because
this file is hand-tuned aphorism content — entries are sized to the
schema's 160-char body budget, which is wider than the project-wide
140-char code limit. Disabling the check here lets content writers
focus on cadence and rhythm without fighting the linter on every
above-average sentence.

Schema per entry:
    id      (str)        Stable unique ID. Never reuse a retired ID.
    title   (str)        Display title.
    body    (str)        Body text. Keep under ~160 chars.
    type    (str)        "info" | "alert" | "warning"
    context (str)        Always "motd" for this source.
    tags    (list[str])  Optional. Day names e.g. ["monday"].
                         Tagged entries only serve on the matching weekday.
=============================================================================
"""

QUOTES: list[dict] = [

    # ── Originals ─────────────────────────────────────────────────────────────
    {
        "id":      "motd-001",
        "title":   "Daily Insight 🧠",
        "body":    "Your biology is your business. We are just here to help you translate it.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-002",
        "title":   "Gut Check 🦠",
        "body":    "Listen to your gut. Literally. It has more neurons than a cat's brain.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-003",
        "title":   "Safety First 🛡️",
        "body":    "Russian Roulette is for casinos, not dinner plates. Scan before you eat.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-004",
        "title":   "Hydration Station 💧",
        "body":    "Drink water, get some sleep, and remember you're basically a highly complex, data-driven unicorn.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-005",
        "title":   "Privacy Matters 🔒",
        "body":    "What happens on this device, stays on this device. Your data is yours alone.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-006",
        "title":   "Food for Thought 🥗",
        "body":    "There is no 'perfect' diet. There is only the diet that works for your unique physiology.",
        "type":    "info",
        "context": "motd",
    },

    # ── The Unicorn Thread ────────────────────────────────────────────────────
    {
        "id":      "motd-007",
        "title":   "Unicorn Wisdom 🦄",
        "body":    "Even unicorns read ingredient labels. Magic is no excuse for maltodextrin.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-008",
        "title":   "Rare & Fabulous 🦄",
        "body":    "Unicorns are rare. So is someone who actually knows what carrageenan does to their gut. Be both.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-009",
        "title":   "Horn First 🦄",
        "body":    "A unicorn never charges blindly into a buffet. Scan first, graze second.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-010",
        "title":   "Stable Conditions 🦄",
        "body":    "Even mythical creatures need a stable microbiome. Yours is built one good meal at a time.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-011",
        "title":   "Glitter & Guts 🦄",
        "body":    "The sparkle is on the outside. The real magic is the 38 trillion microbes keeping you alive.",
        "type":    "info",
        "context": "motd",
    },

    # ── Gut Science ───────────────────────────────────────────────────────────
    {
        "id":      "motd-012",
        "title":   "Microbiome Memo 🦠",
        "body":    "You are 57% non-human by cell count. Feed the majority wisely.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-013",
        "title":   "The Second Brain 🧠",
        "body":    "Your gut produces 95% of your serotonin. Breakfast is, quite literally, a mood decision.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-014",
        "title":   "Bacterial Democracy 🗳️",
        "body":    "Your gut bacteria outvote your taste buds 38 trillion to one. The ayes have it.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-015",
        "title":   "Fermentation Nation 🫙",
        "body":    "Humans have been fermenting food for 10,000 years. Your ancestors were probiotic pioneers.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-016",
        "title":   "Diversity Dividend 🌿",
        "body":    "Studies suggest 30 different plant varieties a week is the sweet spot for microbiome diversity. Consider it a challenge.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-017",
        "title":   "Leaky Logic 🔬",
        "body":    "Your gut lining is only one cell thick. Treat it with the same respect you'd give a very important, very thin wall.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-018",
        "title":   "Gut-Brain Dispatch 📡",
        "body":    "80% of gut-brain signals travel upward, not down. Your gut is talking. Trigzi helps you listen.",
        "type":    "info",
        "context": "motd",
    },

    # ── Dry Wit & Realism ─────────────────────────────────────────────────────
    {
        "id":      "motd-019",
        "title":   "Honest Label 🏷️",
        "body":    "'Natural flavours' is the ingredient list equivalent of 'it's complicated'.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-020",
        "title":   "The Fine Print 🔍",
        "body":    "If the ingredient list requires a chemistry degree, perhaps that is useful information.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-021",
        "title":   "Ultra Processed 🏭",
        "body":    "NOVA Class 4. Two words that do a lot of heavy lifting.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-022",
        "title":   "Marketing Degree 📣",
        "body":    "'Wholesome', 'natural', 'goodness'. None of these are regulated terms. Just so you know.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-023",
        "title":   "Portion of Truth 🍽️",
        "body":    "Serving size: 1/3 of a biscuit. Servings per package: 2.7. We did not make this up.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-024",
        "title":   "Ancient Wisdom 🏺",
        "body":    "Hippocrates said 'let food be thy medicine' around 400 BC. The food industry spent the next 2,400 years stress-testing this theory.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-025",
        "title":   "Colour Theory 🎨",
        "body":    "If your snack is a colour not found in nature, that is a data point worth having.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-026",
        "title":   "Clean Slate 🧹",
        "body":    "'Clean eating' means different things to different bodies. Trigzi helps you define it for yours, not someone else's.",
        "type":    "info",
        "context": "motd",
    },

    # ── Encouragement ─────────────────────────────────────────────────────────
    {
        "id":      "motd-027",
        "title":   "Progress Report 📈",
        "body":    "Every label you scan is a data point. Every data point is a step toward understanding your own body. Keep going.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-028",
        "title":   "Small Wins 🏅",
        "body":    "Swapping one ultra-processed item a week adds up to 52 better decisions a year. The maths are on your side.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-029",
        "title":   "Know Thyself 🪞",
        "body":    "The Oracle at Delphi charged a lot for that advice. We included it in the app.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-030",
        "title":   "Long Game 🎯",
        "body":    "Your microbiome can shift meaningfully in as little as 72 hours with dietary change. The long game starts now.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-031",
        "title":   "No Guilt Here 🚫",
        "body":    "Trigzi is a torch, not a judge. We illuminate. What you do with the light is entirely your call.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-032",
        "title":   "Permission Slip ✅",
        "body":    "You are allowed to eat the thing. You are also allowed to know what is in it first. Both can be true.",
        "type":    "info",
        "context": "motd",
    },

    # ── Philosophical / Offbeat ───────────────────────────────────────────────
    {
        "id":      "motd-033",
        "title":   "Descartes Didn't Eat Well 🧀",
        "body":    "I think, therefore I am. I scan, therefore I know. One of these is more actionable at dinner.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-034",
        "title":   "Ship of Theseus 🚢",
        "body":    "Every cell in your gut lining replaces itself every 3 to 5 days. You are, quite literally, a work in progress.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-035",
        "title":   "Schrödinger's Snack 📦",
        "body":    "Until you scan it, the ingredient list is both fine and not fine. Collapse the uncertainty.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-036",
        "title":   "Entropy & Lunch 🌀",
        "body":    "The universe tends toward disorder. Your gut flora does not have to.",
        "type":    "info",
        "context": "motd",
    },
    {
        "id":      "motd-037",
        "title":   "Occam's Fridge 🧊",
        "body":    "The simplest ingredient list is usually the right one.",
        "type":    "info",
        "context": "motd",
    },

    # ── Day-tagged ────────────────────────────────────────────────────────────
    {
        "id":      "motd-038",
        "title":   "Monday Energy ☀️",
        "body":    "New week. New scan. Your gut does not care what day it is, but we appreciate the optimism.",
        "type":    "info",
        "context": "motd",
        "tags":    ["monday"],
    },
    {
        "id":      "motd-039",
        "title":   "Friday Feeling 🎉",
        "body":    "Weekend incoming. Whatever you eat, at least now you know what's in it. That counts as being responsible.",
        "type":    "info",
        "context": "motd",
        "tags":    ["friday"],
    },
    {
        "id":      "motd-040",
        "title":   "Sunday Reset 🌿",
        "body":    "Sunday is a good day to remember that your body is not punishing you. It is communicating with you.",
        "type":    "info",
        "context": "motd",
        "tags":    ["sunday"],
    },
]
