# Sample prompt: neutral-start emotional response labeling

Draft v1. The numeric units and anchors below are proposed simulation conventions, not biological measurements or established psychological scales. Replace the sample event for each batch item. Keep the rubric and output ordering identical across the batch.

---

## Explanation

You are labeling training examples for a small emotional-response simulation model.

Given an event and a person's starting internal state, produce adjustments to 23 numerical state values. Also generate supplementary labels that may support future models.

This is a simulation of internal responses, not a diagnosis, a biochemical reconstruction, or a prediction of inevitable behavior. Emotion is separate from deliberate reasoning. Internal pressures can influence behavior without deciding it.

For this labeling pass, all 23 starting values are **0.0**. Zero means neutral relative to the simulation's reference point. It does not mean indifference, absence of ordinary needs, or inability to react. Do not infer the original respondent's pre-event state from this assumption.

Predict the initial response once the person registers what happened, before deliberate regulation or later developments. Use the supplied perspective and event details. Do not invent a personality, relationship history, or prior emotional offset.

## Our rubric

Produce one signed floating-point adjustment per value, in exactly this order. Positive means an increase in that particular dimension; negative means a decrease. Neither sign means morally good or bad.

| Index | Value | Decreasing below neutral | Increasing above neutral |
|---|---|---|---|
| 1 | Self-preservation | Reduced concern for one's own safety or integrity | Stronger pressure to protect oneself |
| 2 | Threat vigilance | Less scanning for potential danger | Heightened monitoring for danger |
| 3 | Mobilization | Lower activation and readiness to act | Higher activation and readiness to act |
| 4 | Alarm | Greater immediate ease or reduced alarm | Acute apprehension, fear, or startle |
| 5 | Aversion | Reduced repulsion or rejection pressure | Stronger urge to reject or move away from something |
| 6 | Anger/resistance | Reduced confrontational or oppositional pressure | Stronger pressure to oppose, protest, or confront |
| 7 | Frustration | Reduced strain from obstruction | Stronger strain from blocked effort or goals |
| 8 | Agency | Reduced felt ability to affect the situation | Increased felt ability to influence what happens |
| 9 | Exploration | Reduced interest in investigating the unfamiliar | Stronger curiosity and investigative interest |
| 10 | Reward pursuit | Reduced drive to obtain an anticipated reward | Stronger drive to pursue an anticipated reward |
| 11 | Pleasure | Reduced enjoyment or positive sensation | Increased enjoyment or positive sensation |
| 12 | Contentment | Reduced settled satisfaction | Increased sufficiency, satisfaction, or settled ease |
| 13 | Affiliation | Reduced desire for social connection | Increased desire for social connection |
| 14 | Trust/openness | Increased guardedness or reduced willingness to rely on others | Greater willingness to rely on or open up to others |
| 15 | Social withdrawal | Reduced pressure to disengage socially | Stronger pressure to retreat from social contact |
| 16 | Care | Reduced other-directed concern or nurturing pressure | Increased concern for another's welfare |
| 17 | Separation distress | Reduced distress about disconnection or absence | Increased distress about separation or disconnection |
| 18 | Grief/sadness | Reduced sorrow or sense of loss | Increased sorrow, heaviness, or sense of loss |
| 19 | Recognition seeking | Reduced need to be noticed or acknowledged | Stronger desire for recognition or acknowledgment |
| 20 | Shame/exposure sensitivity | Reduced distress about being negatively seen | Increased vulnerability to exposure or negative evaluation |
| 21 | Guilt/repair motivation | Reduced felt responsibility or pressure to make amends | Increased felt responsibility and pressure to repair perceived wrongdoing |
| 22 | Restoration | Reduced pressure to rest or recover | Increased need for rest, recovery, or recuperation |
| 23 | Play | Reduced playful engagement | Increased playfulness or lighthearted engagement |

These are provisional modeling dimensions, not independent scientific primitives. Some deliberately combine related experiences for v1.

### Adjustment magnitude anchors

Use these as reference magnitudes, not hard caps:

- `0.0`: no meaningful event-driven change supported.
- Around `±0.1`: slight change, barely affecting the person's immediate experience.
- Around `±0.3`: noticeable change, but not dominating their experience.
- Around `±0.6`: strong change, occupying substantial attention or readiness.
- Around `±1.0`: very strong change, dominating the immediate response on that dimension.

Intermediate decimals are allowed. Values beyond the reference anchors require unusually strong support. Precision is for consistent simulation labeling, not a claim of precise human measurement.

## Guidelines for using the rubric

1. Treat the starting state as the stipulated neutral simulation state. A recalled event's original participant did not necessarily begin at neutral.
2. Produce **changes**, not probabilities, appraisal ratings, or final actions. With a zero starting vector, these changes also describe the immediate displacement from neutral.
3. Do not calculate personality multipliers, baseline adjustments, relationship modifiers, decay, caps, saturation, or random noise. Those belong outside this model for now.
4. Ground the response in the event as supplied. A consequential event can matter without inventing personal history. If critical information is missing, identify it in the extras rather than silently filling it in.
5. Several dimensions can increase together, including conflicting ones. Affiliation and withdrawal can coexist; low affiliation does not automatically imply aversion.
6. Do not force opposite signs or a zero-sum total across dimensions. Reduced pleasure is not automatically pain, and increased alarm does not automatically require anger.
7. Use `0.0` for dimensions without a meaningful supported change. Do not introduce negative values merely to balance positive ones. Distinguish insufficient evidence from supported no-change in the extras.
8. Original emotion and appraisal annotations, when provided, are supporting evidence about the original respondent. They are not our numeric targets. Do not directly rescale them into our 23 values or copy their intensity without considering the neutral-start assumption.
9. Preserve perspective: model the specified experiencer, not every person involved and not an observer reading the event.
10. For the primary adjustment vector, choose one coherent, plausible response profile. If substantially different responses are plausible, describe them separately instead of blending incompatible alternatives into a falsely neutral average.
11. Physical responses, urges, attention shifts, and outward reactions are different kinds of labels. Keep them separate. None is mandatory or established as having occurred unless the event says so.
12. Possible reactions are **alternatives**, not a sequence, recommendation, or inevitable outcome. Include restrained or no-outward-response alternatives when plausible. Do not invent numerical probabilities for them.
13. Give short justifications tied to supplied evidence, not a detailed internal reasoning transcript. Express uncertainty honestly. Empty lists are valid when an extra is unsupported.
14. Return valid JSON only, with no Markdown fences, comments, NaN, or explanatory text outside the JSON.

## Event

Record ID: `sample-driving-001`

Perspective: The driver who was cut off.

Starting state, in rubric order:

`[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]`

Scenario:

You are driving on a highway. Another driver cuts in front of you and brakes abruptly, nearly causing a collision. Model the initial response as you register the near collision. No further outcome or personal history is supplied.

Original respondent annotations: Not supplied in this sample. For dataset labeling, insert available annotations here, clearly separated from the scenario.

## Output

Required main training target:

- `adjustments`: exactly 23 finite JSON numbers in rubric order.

Do not output final state values or apply external modifiers.

## Extra output

Generate supplementary labels under `extras`:

- `response_summary`: one short description of the primary internal response.
- `adjustment_notes`: brief evidence-based explanations for the most consequential adjustments; refer to indices 1–23.
- `physical_responses`: plausible bodily sensations or involuntary physical changes.
- `action_tendencies`: urges or readiness to act, without assuming execution.
- `attention_changes`: what becomes salient or harder to ignore.
- `possible_reactions`: distinct plausible outward responses, each with an action and optional verbal/nonverbal expression. Use descriptions rather than writing a full character reply.
- `time_course`: qualitative onset and possible persistence, without calculating decay or inventing exact durations.
- `regulation_opportunities`: plausible actions and their possible effects on internal state. These are candidate simulation labels, not verified interventions.
- `alternative_response_profiles`: up to three materially different plausible internal responses, identifying which dimensions would differ and in what direction. Do not assert calibrated likelihoods.
- `uncertainty`: the main ambiguities, missing information, and weakly supported dimensions.
- `assumptions`: only assumptions actually necessary to produce this label. Do not invent background to populate this field.
- `annotation_tensions`: any disagreement between the neutral-start output and supplied source annotations; otherwise an empty list.

Use this JSON structure. The zero vector and empty fields below illustrate the **format only**, not the correct answer to the event. Replace them with your generated labels.

```json
{
  "adjustments": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
  "extras": {
    "response_summary": "",
    "adjustment_notes": [],
    "physical_responses": [],
    "action_tendencies": [],
    "attention_changes": [],
    "possible_reactions": [],
    "time_course": {
      "onset": "",
      "possible_persistence": ""
    },
    "regulation_opportunities": [],
    "alternative_response_profiles": [],
    "uncertainty": [],
    "assumptions": [],
    "annotation_tensions": []
  }
}
```

For nonempty structured lists, use these item shapes:

```json
{
  "adjustment_notes": {"dimension_index": 1, "justification": "Brief supporting evidence"},
  "possible_reactions": {"action": "Possible outward action", "expression": "Possible expression, or null"},
  "regulation_opportunities": {"action": "Candidate action", "possible_effect": "Possible internal effect"},
  "alternative_response_profiles": {"summary": "Alternative internal response", "differences": [{"dimension_index": 1, "relative_to_primary": "higher or lower", "explanation": "Brief explanation"}]}
}
```

All other list fields contain strings. Replace instructional placeholders; do not copy them literally.
