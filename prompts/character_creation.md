# Character Profile Extraction

Extract the character's core details from the backstory provided by the player. Focus on identifying:

## Extraction Guidelines

1. **Origin Faction**: The faction, race, class, guild, or group the character belongs to. This could be something like "Shadowborn", "Order of the Sun", "Drow Elf", "Iron Legion", etc.

2. **Motivation**: A single-word tag representing their core drive. Choose from:
   - Core drives: Revenge, Wealth, Power, Love, Knowledge, Freedom, Justice, Vengeance, Redemption, Atonement, Glory, Truth, Peace, Chaos, Order
   - Or create a new one-word tag if none fit (e.g., "Revenge", "Power", "Redemption")

3. **Goal**: Their specific, actionable objective. This should be concrete and directed, not vague aspirations. Examples:
   - "Destroy the Dark Lord's fortress"
   - "Find the lost artifact of their ancestors"
   - "Avenger their brother's death"
   - "Rebuild the fallen citadel"

## Examples

**Input**: "I am a ranger of the Whispering Woods, driven by hatred for those who burned my village, seeking to find and destroy the warlord responsible."

**Output**:
- origin_faction: "Ranger of the Whispering Woods"
- motivation: "Hatred"
- goal: "Destroy the warlord who burned the village"

**Input**: "A former knight seeking redemption for the sins of their past, they wander the lands in search of penance."

**Output**:
- origin_faction: "Former Knight"
- motivation: "Redemption"
- goal: "Find penance for past sins"

**Input**: "I seek knowledge of the ancient magic that once ruled this world, before it was lost to time."

**Output**:
- origin_faction: "Arcane Scholar"
- motivation: "Knowledge"
- goal: "Uncover the ancient magic of the lost age"

**Input**: "Merchant of the desert caravans, chasing fortune through the silk roads."

**Output**:
- origin_faction: "Desert Caravan Merchant"
- motivation: "Wealth"
- goal: "Accumulate fortune along the silk roads"

## Important Notes

- Always extract all three fields, even if some need to be inferred
- Keep motivation as a single word (no compound adjectives)
- Make goals actionable and specific
- Default origin_faction to " wanderer" if unclear