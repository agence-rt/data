"""Ligne de commande : python -m agoa_data "198 rue Championnet, Paris" [dossier_sortie]"""
import os
import sys

from . import pipeline, export


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    sortie = argv[1] if len(argv) > 1 else os.path.join(os.path.expanduser("~"), "AGOA DATA")
    res = pipeline.analyser(argv[0], sortie, progress=print, cache=os.path.join(sortie, "_cache"))
    print()
    print(export.synthese_texte(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
