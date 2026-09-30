# The Coaching Staff — installation & receipt

## Installation

1. Create the folder `coaching-staff/`
2. Save the skill as `coaching-staff/SKILL.md`
3. Load it into Claude (via Skills, Projects, or paste it as a system prompt)
4. Say: "Build me a coaching staff for my [business]"

In this repo it lives at `.claude/skills/coaching-staff/`, so Claude Code picks
it up automatically as a project skill.

## Receipt for this skill

```
CLAIM:        This skill takes any business or domain and produces a complete
              verification SOP using the coaching-staff metaphor and the
              five-skill stack.
PRODUCER:     One model, this session.
CHECKER:      None independent yet.
INDEPENDENCE: Collapse points — model, source, implementation.
EVIDENCE:     Skill is self-contained, installable, and walks the user
              through eight SOP sections with copy-paste prompts.
BOUNDARY:     Untested on real users. The compounding rubric assumes
              the user actually reviews every 10 decisions. If they don't,
              the loop does not close.
STATUS:       PARTIALLY VERIFIED
NEXT CHECK:   Install it. Run it on a real business. Does the user produce
              a receipt that survives expert review?
NEXT QUESTION: Which section produces the most resistance from users?
              That's the section that needs simplification.

⚠️ Do not forget to verify with another model. This does not replace human expertise.
```
