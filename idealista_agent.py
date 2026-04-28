import csv
import json
import re
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CONFIG_FILE = Path("config.json")
SEEN_FILE = Path("idealista_vistos.json")
HISTORICO_FILE = Path("historico_oportunidades.csv")

HEADERS = {"User-Agent": "Mozilla/5.0"}

PALABRAS_RIESGO = [
    "bajo",
    "ocupado",
    "ocupada",
    "nuda propiedad",
    "usufructo",
    "subasta",
    "alquilado",
    "arrendado",
    "sin ascensor"
]


def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def load_seen():
    return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))


def save_seen(seen):
    SEEN_FILE.write_text(
        json.dumps(sorted(seen), indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def number(text):
    text = text.replace(".", "").replace(",", "")
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def detect_zone(text, config):
    text = text.lower()
    for zona in config["precios_zona"]:
        if zona in text:
            return zona
    return None


def notify(topic, msg):
    r = requests.post(
        f"https://ntfy.sh/{topic}",
        data=msg.encode("utf-8"),
        headers={
            "Title": "Chollo Idealista Madrid",
            "Priority": "high",
            "Tags": "house,moneybag"
        },
        timeout=20
    )
    print("NTFY:", r.status_code)


def score_ad(titulo, texto, precio, metros, config):
    full = f"{titulo} {texto}".lower()

    if any(z in full for z in config["zonas_excluidas"]):
        return 0, "zona excluida", None, None, []

    riesgos = [p for p in PALABRAS_RIESGO if p in full]

    if "bajo" in riesgos:
        return 0, "bajo descartado", None, None, riesgos

    precio_m2 = precio / metros
    zona = detect_zone(full, config)

    score = 0

    if config["precio_min"] <= precio <= config["precio_max"]:
        score += 2

    if metros >= config["metros_min"]:
        score += 2

    if "ático" in full or "ultima planta" in full or "última planta" in full:
        score += 2
    elif "planta" in full:
        score += 1

    if "ascensor" in full:
        score += 1

    if "a reformar" in full or "para reformar" in full:
        score += 1

    descuento = None

    if zona:
        media = config["precios_zona"][zona]
        descuento = 1 - (precio_m2 / media)

        if descuento >= 0.25:
            score += 4
        elif descuento >= 0.20:
            score += 3
        elif descuento >= 0.15:
            score += 2

    score -= len(riesgos)

    return score, "ok", zona, descuento, riesgos


def append_history(row):
    exists = HISTORICO_FILE.exists()

    with HISTORICO_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not exists:
            writer.writerow([
                "fecha", "id", "titulo", "precio", "metros",
                "precio_m2", "zona", "descuento", "score", "link"
            ])

        writer.writerow(row)


def main():
    config = load_config()
    seen = load_seen()

    r = requests.get(config["url"], headers=HEADERS, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    ads = soup.select("article")

    print("Anuncios detectados:", len(ads))

    nuevas_alertas = 0

    for ad in ads:
        link_tag = ad.select_one("a.item-link")
        price_tag = ad.select_one(".item-price")

        if not link_tag or not price_tag:
            continue

        link = "https://www.idealista.com" + link_tag.get("href")
        ad_id = link.split("/")[-2] if "/" in link else link

        if ad_id in seen:
            continue

        titulo = link_tag.get_text(" ", strip=True)
        texto = ad.get_text(" ", strip=True)

        precio = number(price_tag.get_text())
        if not precio:
            continue

        metros = None
        for d in ad.select(".item-detail"):
            t = d.get_text(" ", strip=True).lower()
            if "m²" in t or "m2" in t:
                metros = number(t)
                break

        if not metros:
            continue

        seen.add(ad_id)

        if precio < config["precio_min"] or precio > config["precio_max"]:
            continue

        if metros < config["metros_min"]:
            continue

        score, motivo, zona, descuento, riesgos = score_ad(
            titulo, texto, precio, metros, config
        )

        precio_m2 = precio / metros

        if score < config["score_minimo"]:
            print("Descartado:", titulo, "score", score, motivo)
            continue

        descuento_txt = f"{descuento:.1%}" if descuento is not None else "No calculado"

        msg = f"""🔥 Posible chollo Idealista Madrid

{titulo}

Precio: {precio:,.0f} €
Metros: {metros} m²
€/m²: {precio_m2:,.0f} €
Zona: {zona or "No detectada"}
Descuento estimado: {descuento_txt}
Score: {score}

Riesgos: {", ".join(riesgos) if riesgos else "ninguno"}

{link}
"""

        notify(config["ntfy_topic"], msg)

        append_history([
            datetime.utcnow().isoformat(),
            ad_id,
            titulo,
            precio,
            metros,
            round(precio_m2, 2),
            zona or "",
            round(descuento, 4) if descuento is not None else "",
            score,
            link
        ])

        nuevas_alertas += 1

    save_seen(seen)

    print("Nuevas alertas:", nuevas_alertas)


if __name__ == "__main__":
    main()
