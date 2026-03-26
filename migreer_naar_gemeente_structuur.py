#!/usr/bin/env python3
"""
Raadslens - Migratie naar gemeente-gebaseerde mapstructuur.

Verplaatst bestaande Texel-bestanden van de oude structuur:
  docs/feed.xml
  docs/seen.json
  docs/transcripties/
  docs/namen_cache.json
  docs/vocabulary_cache.json

Naar de nieuwe structuur:
  docs/texel/feed.xml
  docs/texel/seen.json
  docs/texel/transcripties/
  docs/texel/namen_cache.json
  docs/texel/vocabulary_cache.json

Veilig om meerdere keren te draaien - slaat over als doel al bestaat.
"""
import shutil
from pathlib import Path


def migreer(bron, doel):
    bron = Path(bron)
    doel = Path(doel)
    if not bron.exists():
        print(f"  Overgeslagen (bestaat niet): {bron}")
        return
    if doel.exists():
        print(f"  Overgeslagen (doel bestaat al): {doel}")
        return
    doel.parent.mkdir(parents=True, exist_ok=True)
    if bron.is_dir():
        shutil.copytree(bron, doel)
        print(f"  Map gekopieerd: {bron} -> {doel}")
    else:
        shutil.copy2(bron, doel)
        print(f"  Bestand gekopieerd: {bron} -> {doel}")


def main():
    print("=== Raadslens Migratie ===")
    print("Texel bestanden verplaatsen naar docs/texel/...\n")

    migraties = [
        ("docs/feed.xml",             "docs/texel/feed.xml"),
        ("docs/seen.json",            "docs/texel/seen.json"),
        ("docs/transcripties",        "docs/texel/transcripties"),
        ("docs/namen_cache.json",     "docs/texel/namen_cache.json"),
        ("docs/vocabulary_cache.json","docs/texel/vocabulary_cache.json"),
    ]

    for bron, doel in migraties:
        migreer(bron, doel)

    print("\nMigratie klaar!")
    print("\nControleer of alles klopt in docs/texel/")
    print("Verwijder daarna handmatig de oude bestanden als alles goed is:")
    print("  docs/feed.xml")
    print("  docs/seen.json")
    print("  docs/transcripties/")
    print("  docs/namen_cache.json")
    print("  docs/vocabulary_cache.json")


if __name__ == "__main__":
    main()
