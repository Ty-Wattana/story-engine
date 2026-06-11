"""Tests for the lore validation system with LLM-based validation."""
import sys
import os
sys.path.insert(0, os.path.abspath("."))

from src.lore_validator import (
    LoreParser,
    LoreValidator,
    LoreConflict,
    LLMValidationError,
    create_validator
)
from pathlib import Path
from src.llm_client import LLMClient

def test_parsing():
    """Test that lore file parses correctly."""
    print("=== Test 1: Parsing lore file ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))

    print(f"Facts parsed: {len(parser.db.facts)}")
    print(f"Categories found: {parser.db.categories}")
    print(f"Number of lore entries: {len(parser.db.lore_summary.split(chr(10)))}")

    # Show some facts
    for fact in parser.db.facts[:5]:
        print(f"  - {fact.category}: {fact.fact[:60]}...")

    print()


def test_llm_validation():
    """Test that LLM validation catches sci-fi conflicts."""
    print("=== Test 2: LLM Validation (Sci-Fi Character) ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))
    validator = LoreValidator(parser)

    # Test with a sci-fi input that should fail validation
    print("Input: 'A powerful elf from the Iron Circle who built a steam engine'")
    error = validator.validate_input("A powerful elf from the Iron Circle who built a steam engine")

    print(f"Is valid: {error.is_valid}")
    print(f"Conflicts found: {len(error.conflicts)}")

    if error.conflicts:
        for conflict in error.conflicts:
            print(f"  Type: {conflict.conflict_type}")
            print(f"  Message: {conflict.conflict_message}")
            if conflict.suggestion:
                print(f"  Suggestion: {conflict.suggestion}")
        print()

    # Test with a valid fantasy input
    print("Input: 'A brave knight seeking glory in battle'")
    error2 = validator.validate_input("A brave knight seeking glory in battle")

    print(f"Is valid: {error2.is_valid}")
    print(f"Conflicts found: {len(error2.conflicts)}")
    print()


def test_forbidden_magic():
    """Test that forbidden magic is detected."""
    print("=== Test 3: Forbidden Magic Detection ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))
    validator = LoreValidator(parser)

    # Test forbidden magic
    print("Input: 'I control time and death'")
    error = validator.validate_input("I control time and death")

    print(f"Is valid: {error.is_valid}")
    print(f"Conflicts found: {len(error.conflicts)}")

    if error.conflicts:
        for conflict in error.conflicts:
            print(f"  Message: {conflict.conflict_message}")
    print()


def test_technology_violation():
    """Test that forbidden technology is detected."""
    print("=== Test 4: Technology Violation ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))
    validator = LoreValidator(parser)

    # Test with gunpowder
    print("Input: 'I built a steam engine and cannon'")
    error = validator.validate_input("I built a steam engine and cannon")

    print(f"Is valid: {error.is_valid}")
    print(f"Conflicts found: {len(error.conflicts)}")

    if error.conflicts:
        for conflict in error.conflicts:
            print(f"  Type: {conflict.conflict_type}")
            print(f"  Message: {conflict.conflict_message}")
            if conflict.suggestion:
                print(f"  Suggestion: {conflict.suggestion}")
    print()


def test_valid_character():
    """Test that valid fantasy characters pass validation."""
    print("=== Test 5: Valid Fantasy Character ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))
    validator = LoreValidator(parser)

    # Test various valid inputs
    valid_inputs = [
        "A freeborn villager seeking survival",
        "A knight of the Iron Circle seeking redemption",
        "A mage who wields rare and taxing magic",
        "A mercenary of the guild seeking wealth",
    ]

    for input_text in valid_inputs:
        print(f"Input: {input_text}")
        error = validator.validate_input(input_text)
        print(f"  Is valid: {error.is_valid}")
        if error.conflicts:
            for conflict in error.conflicts:
                print(f"  Conflict: {conflict.conflict_message}")
        print()


def test_negotiation_flow():
    """Test the negotiation flow when conflicts are found."""
    print("=== Test 6: Negotiation Flow ===\n")

    parser = LoreParser()
    parser.parse_markdown(Path("data/lore_summary.md"))
    validator = LoreValidator(parser)

    # Create a conflict
    error = validator.validate_input("I built a rocket powered by gunpowder")

    print(f"Initial input has {len(error.conflicts)} conflict(s)")

    if error.conflicts:
        # Show what suggestions are available
        print("\nSuggested revisions:")
        for conflict in error.conflicts:
            if conflict.suggestion:
                print(f"  - {conflict.suggestion}")

    print()


def main():
    """Run all tests."""
    print("=" * 60)
    print("LORE VALIDATION SYSTEM TESTS (LLM-Based)")
    print("=" * 60)
    print()

    test_parsing()
    test_llm_validation()
    test_forbidden_magic()
    test_technology_violation()
    test_valid_character()
    test_negotiation_flow()

    print("=" * 60)
    print("All tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
