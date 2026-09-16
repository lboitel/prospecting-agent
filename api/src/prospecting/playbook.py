"""Chargement du playbook commercial (offre, ICP, ton) depuis des fichiers Markdown."""

from prospecting.config import get_settings

PLAYBOOK_FILES = ("offer.md", "icp.md", "tone.md")


def load_playbook() -> str:
    # Lu à chaque appel : une modification du playbook s'applique sans redémarrage.
    # Le contenu est stable entre deux appels, donc il reste en cache côté API.
    directory = get_settings().playbook_dir
    sections = []
    for name in PLAYBOOK_FILES:
        path = directory / name
        if path.exists():
            sections.append(f'<document name="{name}">\n{path.read_text().strip()}\n</document>')
    return "\n\n".join(sections)
