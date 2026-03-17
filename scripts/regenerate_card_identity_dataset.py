from __future__ import annotations

import hashlib
import json
import math
import os
import random
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
CARD_COLLECTION = "01_french_id"
CARD_ROOT = ROOT / "card_identity" / CARD_COLLECTION
IMAGE_ROOT = CARD_ROOT / "images"
GROUND_TRUTH_ROOT = CARD_ROOT / "ground_truth"
TMP_MANIFEST = ROOT / "scripts" / "_card_identity_render_manifest.json"

CARD_WIDTH = 1400
CARD_HEIGHT = 900
BASE_CANVAS_WIDTH = 1600
BASE_CANVAS_HEIGHT = 1050
VARIANT_CANVAS_WIDTH = 1800
VARIANT_CANVAS_HEIGHT = 1300

CARD_SOURCE_QUAD = np.array(
    [[0.0, 0.0], [CARD_WIDTH, 0.0], [CARD_WIDTH, CARD_HEIGHT], [0.0, CARD_HEIGHT]],
    dtype=float,
)

FIELD_RECTS = {
    "nom": {"x": 560, "y": 185, "width": 470, "height": 60},
    "prenom": {"x": 560, "y": 345, "width": 470, "height": 60},
    "nationalite": {"x": 770, "y": 478, "width": 245, "height": 42},
    "date_naissance": {"x": 500, "y": 620, "width": 330, "height": 55},
    "sexe": {"x": 635, "y": 700, "width": 80, "height": 55},
}

ALLOWED_FAMILIES = ["CA", "CS", "HA", "HS", "KA", "KS", "PA", "PS", "TA", "TS"]

FAMILY_SPECS = {
    "CA": {
        "center": (860.0, 640.0),
        "scale": 0.93,
        "angle": -3.0,
        "shear_x": -0.025,
        "shear_y": 0.008,
    },
    "CS": {
        "center": (930.0, 640.0),
        "scale": 0.93,
        "angle": 3.5,
        "shear_x": 0.024,
        "shear_y": -0.010,
    },
    "HA": {
        "center": (870.0, 600.0),
        "scale": 0.91,
        "angle": -1.5,
        "shear_x": -0.032,
        "shear_y": 0.016,
    },
    "HS": {
        "center": (930.0, 600.0),
        "scale": 0.91,
        "angle": 1.8,
        "shear_x": 0.030,
        "shear_y": -0.014,
    },
    "KA": {
        "center": (850.0, 690.0),
        "scale": 0.95,
        "angle": -7.0,
        "shear_x": -0.018,
        "shear_y": 0.028,
    },
    "KS": {
        "center": (950.0, 690.0),
        "scale": 0.95,
        "angle": 7.0,
        "shear_x": 0.018,
        "shear_y": -0.026,
    },
    "PA": {
        "center": (820.0, 640.0),
        "scale": 0.90,
        "angle": -10.0,
        "shear_x": -0.040,
        "shear_y": 0.010,
    },
    "PS": {
        "center": (980.0, 640.0),
        "scale": 0.90,
        "angle": 10.0,
        "shear_x": 0.038,
        "shear_y": -0.008,
    },
    "TA": {
        "center": (880.0, 575.0),
        "scale": 0.88,
        "angle": -4.0,
        "shear_x": -0.028,
        "shear_y": 0.032,
    },
    "TS": {
        "center": (920.0, 725.0),
        "scale": 0.97,
        "angle": 4.5,
        "shear_x": 0.022,
        "shear_y": -0.030,
    },
}

FEMALE_FIRST_NAMES = [
    "Sophie",
    "Camille",
    "Louise",
    "Emma",
    "Lea",
    "Manon",
    "Clara",
    "Sarah",
    "Julie",
    "Chloe",
    "Alice",
    "Nina",
    "Ines",
    "Eva",
    "Lucie",
    "Marie",
    "Elise",
    "Anna",
    "Mila",
    "Jade",
]

MALE_FIRST_NAMES = [
    "Lucas",
    "Hugo",
    "Louis",
    "Nathan",
    "Leo",
    "Jules",
    "Arthur",
    "Gabriel",
    "Tom",
    "Paul",
    "Theo",
    "Enzo",
    "Mathis",
    "Noah",
    "Adam",
    "Maxime",
    "Antoine",
    "Romain",
    "Baptiste",
    "Alexis",
]

LAST_NAMES = [
    "Durand",
    "Martin",
    "Bernard",
    "Dubois",
    "Thomas",
    "Robert",
    "Richard",
    "Petit",
    "Garcia",
    "Moreau",
    "Fournier",
    "Roux",
    "David",
    "Bertrand",
    "Morel",
    "Simon",
    "Laurent",
    "Lefebvre",
    "Michel",
    "Girard",
    "Andre",
    "Mercier",
    "Dupont",
    "Lambert",
    "Bonnet",
    "Francois",
    "Martinez",
    "Legrand",
    "Garnier",
    "Faure",
    "Rousseau",
    "Blanc",
    "Henry",
    "Roussel",
    "Muller",
    "Perrin",
    "Morin",
    "Mathieu",
    "Clement",
    "Masson",
]


@dataclass
class Profile:
    record_id: str
    nom: str
    prenom: str
    date_naissance: str
    nationalite: str
    sexe: str
    portrait_variant: str
    card_number: str
    signature: str
    is_minor: bool


def deterministic_seed(value: str) -> int:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def rect_to_quad(rect: dict[str, int]) -> np.ndarray:
    x = float(rect["x"])
    y = float(rect["y"])
    width = float(rect["width"])
    height = float(rect["height"])
    return np.array(
        [
            [x, y],
            [x + width, y],
            [x + width, y + height],
            [x, y + height],
        ],
        dtype=float,
    )


def solve_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    matrix = []
    vector = []
    for (x, y), (xp, yp) in zip(src, dst):
        matrix.append([x, y, 1.0, 0.0, 0.0, 0.0, -x * xp, -y * xp])
        matrix.append([0.0, 0.0, 0.0, x, y, 1.0, -x * yp, -y * yp])
        vector.append(xp)
        vector.append(yp)
    solution = np.linalg.solve(np.array(matrix, dtype=float), np.array(vector, dtype=float))
    return np.array(
        [
            [solution[0], solution[1], solution[2]],
            [solution[3], solution[4], solution[5]],
            [solution[6], solution[7], 1.0],
        ],
        dtype=float,
    )


def transform_quad(matrix: np.ndarray, quad: np.ndarray) -> list[list[int]]:
    transformed = []
    for x, y in quad:
        vec = matrix @ np.array([x, y, 1.0], dtype=float)
        vec = vec / vec[2]
        transformed.append([int(round(vec[0])), int(round(vec[1]))])
    return transformed


def format_date(day: int, month: int, year: int) -> str:
    return f"{day:02d}/{month:02d}/{year:04d}"


def build_profile(record_id: str, index: int) -> Profile:
    if index == 0:
        return Profile(
            record_id=record_id,
            nom="DURAND",
            prenom="Sophie",
            date_naissance="15/09/2009",
            nationalite="Francaise",
            sexe="F",
            portrait_variant="minor",
            card_number="123456789012",
            signature="Sophie Durand",
            is_minor=True,
        )

    is_minor = index % 5 == 0
    sexe = "F" if index % 2 == 0 else "M"
    first_names = FEMALE_FIRST_NAMES if sexe == "F" else MALE_FIRST_NAMES
    prenom = first_names[(index * 3 + (0 if sexe == "F" else 1)) % len(first_names)]
    nom = LAST_NAMES[(index * 7 + 5) % len(LAST_NAMES)].upper()

    if is_minor:
        year = 2008 + ((index * 7) % 5)
    else:
        year = 1962 + ((index * 11) % 44)
    month = 1 + ((index * 5) % 12)
    day = 1 + ((index * 7) % 28)

    if sexe == "F":
        nationalite = "Francaise"
        portrait_variant = ["major", "major_mirror", "minor", "minor_warm"][index % 4]
    else:
        nationalite = "Francais"
        portrait_variant = ["major_cool", "minor_mirror", "major_gray", "minor_cool"][index % 4]

    card_number = f"{(100000000000 + deterministic_seed(record_id) % 899999999999):012d}"

    return Profile(
        record_id=record_id,
        nom=nom,
        prenom=prenom,
        date_naissance=format_date(day, month, year),
        nationalite=nationalite,
        sexe=sexe,
        portrait_variant=portrait_variant,
        card_number=card_number,
        signature=f"{prenom} {nom.title()}",
        is_minor=is_minor,
    )


def generate_variant_quad(family: str, sequence_number: int) -> list[list[int]]:
    spec = FAMILY_SPECS[family]
    rng = random.Random(deterministic_seed(f"{family}-{sequence_number}"))

    scale_x = spec["scale"] + rng.uniform(-0.035, 0.035)
    scale_y = spec["scale"] + rng.uniform(-0.035, 0.035)
    shear_x = spec["shear_x"] + rng.uniform(-0.012, 0.012)
    shear_y = spec["shear_y"] + rng.uniform(-0.012, 0.012)
    angle = math.radians(spec["angle"] + rng.uniform(-3.0, 3.0))
    center_x = spec["center"][0] + rng.uniform(-35.0, 35.0)
    center_y = spec["center"][1] + rng.uniform(-28.0, 28.0)

    source_corners = np.array(
        [
            [-CARD_WIDTH / 2.0, -CARD_HEIGHT / 2.0],
            [CARD_WIDTH / 2.0, -CARD_HEIGHT / 2.0],
            [CARD_WIDTH / 2.0, CARD_HEIGHT / 2.0],
            [-CARD_WIDTH / 2.0, CARD_HEIGHT / 2.0],
        ],
        dtype=float,
    )

    rotation = np.array(
        [
            [math.cos(angle), -math.sin(angle)],
            [math.sin(angle), math.cos(angle)],
        ],
        dtype=float,
    )
    shear_scale = np.array(
        [
            [scale_x, shear_x],
            [shear_y, scale_y],
        ],
        dtype=float,
    )
    affine = shear_scale @ rotation.T
    corners = source_corners @ affine.T
    corners += np.array([center_x, center_y], dtype=float)

    margin = 70.0
    min_x = float(corners[:, 0].min())
    max_x = float(corners[:, 0].max())
    min_y = float(corners[:, 1].min())
    max_y = float(corners[:, 1].max())

    shift_x = 0.0
    shift_y = 0.0
    if min_x < margin:
        shift_x += margin - min_x
    if max_x > VARIANT_CANVAS_WIDTH - margin:
        shift_x -= max_x - (VARIANT_CANVAS_WIDTH - margin)
    if min_y < margin:
        shift_y += margin - min_y
    if max_y > VARIANT_CANVAS_HEIGHT - margin:
        shift_y -= max_y - (VARIANT_CANVAS_HEIGHT - margin)

    corners += np.array([shift_x, shift_y], dtype=float)

    return [[int(round(x)), int(round(y))] for x, y in corners]


def build_json_payload(record_id: str, quad: list[list[int]], profile: Profile) -> dict:
    matrix = solve_homography(CARD_SOURCE_QUAD, np.array(quad, dtype=float))

    fields = {}
    for field_name, rect in FIELD_RECTS.items():
        transformed_quad = transform_quad(matrix, rect_to_quad(rect))
        fields[field_name] = {
            "value": getattr(profile, field_name),
            "quad": transformed_quad,
        }

    return {
        "schema_version": "french_cni_minimal_v1",
        "document_type": "french_cni_sim",
        "record_id": record_id,
        "document_quad": quad,
        "fields": fields,
    }


def write_annotation(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def cleanup_tree() -> None:
    allowed_files = {
        IMAGE_ROOT / f"{CARD_COLLECTION}.tif",
        GROUND_TRUTH_ROOT / f"{CARD_COLLECTION}.json",
    }

    for family in ALLOWED_FAMILIES:
        for index in range(1, 31):
            stem = f"{family}01_{index:02d}"
            allowed_files.add(IMAGE_ROOT / family / f"{stem}.tif")
            allowed_files.add(GROUND_TRUTH_ROOT / family / f"{stem}.json")

    for folder in [IMAGE_ROOT, GROUND_TRUTH_ROOT]:
        for file_path in sorted(folder.rglob("*")):
            if file_path.is_file() and file_path not in allowed_files:
                file_path.unlink()

    for folder in sorted(GROUND_TRUTH_ROOT.rglob("*"), reverse=True):
        if folder.is_dir() and folder.name == ".DS_Store":
            folder.unlink()


def remove_ds_store_files() -> None:
    for ds_store in CARD_ROOT.rglob(".DS_Store"):
        ds_store.unlink()


def build_manifest(entries: list[dict]) -> dict:
    return {
        "card_width": CARD_WIDTH,
        "card_height": CARD_HEIGHT,
        "layout": {
            "field_rects": FIELD_RECTS,
        },
        "entries": entries,
    }


def main() -> None:
    remove_ds_store_files()
    cleanup_tree()

    manifest_entries = []
    image_count = 0
    annotation_count = 0
    minor_count = 0
    female_count = 0
    male_count = 0

    base_record_id = CARD_COLLECTION
    base_profile = build_profile(base_record_id, 0)
    base_quad = [[100, 75], [1500, 75], [1500, 975], [100, 975]]
    base_payload = build_json_payload(base_record_id, base_quad, base_profile)
    write_annotation(GROUND_TRUTH_ROOT / f"{CARD_COLLECTION}.json", base_payload)
    annotation_count += 1
    image_count += 1
    minor_count += int(base_profile.is_minor)
    female_count += int(base_profile.sexe == "F")
    male_count += int(base_profile.sexe == "M")
    manifest_entries.append(
        {
            "record_id": base_record_id,
            "canvas_width": BASE_CANVAS_WIDTH,
            "canvas_height": BASE_CANVAS_HEIGHT,
            "image_path": str(IMAGE_ROOT / f"{CARD_COLLECTION}.tif"),
            "document_quad": base_quad,
            "profile": base_profile.__dict__,
        }
    )

    running_index = 1
    for family in ALLOWED_FAMILIES:
        for sample_index in range(1, 31):
            record_id = f"{family}01_{sample_index:02d}"
            profile = build_profile(record_id, running_index)
            quad = generate_variant_quad(family, sample_index)
            payload = build_json_payload(record_id, quad, profile)
            write_annotation(GROUND_TRUTH_ROOT / family / f"{record_id}.json", payload)
            manifest_entries.append(
                {
                    "record_id": record_id,
                    "canvas_width": VARIANT_CANVAS_WIDTH,
                    "canvas_height": VARIANT_CANVAS_HEIGHT,
                    "image_path": str(IMAGE_ROOT / family / f"{record_id}.tif"),
                    "document_quad": quad,
                    "profile": profile.__dict__,
                }
            )
            annotation_count += 1
            image_count += 1
            minor_count += int(profile.is_minor)
            female_count += int(profile.sexe == "F")
            male_count += int(profile.sexe == "M")
            running_index += 1

    TMP_MANIFEST.write_text(json.dumps(build_manifest(manifest_entries), indent=2), encoding="utf-8")

    try:
        subprocess.run(
            [
                "clang",
                "-fobjc-arc",
                "-framework",
                "Foundation",
                "-framework",
                "AppKit",
                "-framework",
                "CoreImage",
                "-framework",
                "CoreGraphics",
                str(ROOT / "scripts" / "render_card_identity.m"),
                "-o",
                str(ROOT / "scripts" / "_render_card_identity"),
            ],
            check=True,
            cwd=ROOT,
        )
        subprocess.run(
            [
                str(ROOT / "scripts" / "_render_card_identity"),
                str(TMP_MANIFEST),
            ],
            check=True,
            cwd=ROOT,
        )
    finally:
        if TMP_MANIFEST.exists():
            TMP_MANIFEST.unlink()
        renderer_binary = ROOT / "scripts" / "_render_card_identity"
        if renderer_binary.exists():
            renderer_binary.unlink()

    print(
        json.dumps(
            {
                "image_files": image_count,
                "annotation_files": annotation_count,
                "female_records": female_count,
                "male_records": male_count,
                "minor_records": minor_count,
                "adult_records": annotation_count - minor_count,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
