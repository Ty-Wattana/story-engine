"""Tests for the lore validation system."""
import sys
import os
sys.path.insert(0, os.path.abspath("."))

from src.lore_validator import LoreParser, LoreValidator, create_validator
from pathlib import Path

def test_parsing():
    """Test that lore file parses correctly."""
    print("=== Test 1: Parsing lore file ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))

    print(f"Facts parsed: {len(parser.db.facts)}")
    print(f"Categories found: {parser.db.categories}")
    print(f"Constraints: {len(parser.db.constraints)}")

    # Show some facts
    for fact in parser.db.facts[:5]:
        print(f"  - {fact.category}: {fact.fact[:60]}...")

    print()

def test_validation():
    """Test that validation catches lore conflicts."""
    print("=== Test 2: Validating inputs ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))
    validator = LoreValidator(parser)

    # Test 2a: Valid input
    print("2a. Valid input: 'A freeborn villager seeking survival'")
    is_valid, conflicts, suggestions = validator.validate_input("A freeborn villager seeking survival")
    print(f"   Is valid: {is_valid}")
    print(f"   Conflicts: {len(conflicts)}")
    print()

    # Test 2b: Invalid faction
    print("2b. Invalid faction: 'A powerful elf of ancient blood'")
    is_valid, conflicts, suggestions = validator.validate_input("A powerful elf of ancient blood")
    print(f"   Is valid: {is_valid}")
    print(f"   Conflicts: {len(conflicts)}")
    if conflicts:
        for c in conflicts:
            print(f"     - {c.conflict}")
    print()

    # Test 2c: Forbidden magic
    print("2c. Forbidden magic: 'I control time and death'")
    is_valid, conflicts, suggestions = validator.validate_input("I control time and death")
    print(f"   Is valid: {is_valid}")
    print(f"   Conflicts: {len(conflicts)}")
    if conflicts:
        for c in conflicts:
            print(f"     - {c.conflict}")
    print()

    # Test 2d: Forbidden technology
    print("2d. Forbidden tech: 'I built a steam engine'")
    is_valid, conflicts, suggestions = validator.validate_input("I built a steam engine")
    print(f"   Is valid: {is_valid}")
    print(f"   Conflicts: {len(conflicts)}")
    if conflicts:
        for c in conflicts:
            print(f"     - {c.conflict}")
    print()

def test_relational_queries():
    """Test that we can find relevant lore for an input."""
    print("=== Test 3: Finding relevant lore ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))

    # Test finding lore about magic
    print("Input: 'I want to use magic but it's dangerous'")
    relevant = parser.db.find_related_facts("magic dangerous")
    for fact in relevant[:3]:
        print(f"  - {fact.category}: {fact.fact}")
    print()

    # Test finding lore about factions
    print("Input: 'I join the Iron Circle'")
    relevant = parser.db.find_related_facts("Iron Circle faction")
    for fact in relevant[:3]:
        print(f"  - {fact.category}: {fact.fact}")
    print()

def main():
    """Run all tests."""
    print("=" * 50)
    print("LORE VALIDATION SYSTEM TESTS")
    print("=" * 50)
    print()

    test_parsing()
    test_validation()
    test_relational_queries()

    print("=" * 50)
    print("All tests completed!")
    print("=" * 50)

if __name__ == "__main__":
    main()
