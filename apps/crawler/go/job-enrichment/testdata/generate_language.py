"""Freeze language behavior using the current locked Python model, offline."""

from __future__ import annotations

import ast
import hashlib
import json
import random
from pathlib import Path

from fast_langdetect import detect
from fast_langdetect.infer import _LOCAL_SMALL_MODEL_PATH, LangDetectConfig, LangDetector

from src.shared.langdetect import _detect_all_languages_python, _detect_language_python

module = Path(__file__).resolve().parents[1]
model = _LOCAL_SMALL_MODEL_PATH.read_bytes()
assert (
    hashlib.sha256(model).hexdigest()
    == "8f3472cfe8738a7b6099e8e999c3cbfae0dcd15696aac7d7738a8039db603e83"
)
assert LangDetectConfig().max_input_length == 80
texts = [
    "",
    "Hello world",
    "<p></p>",
    " " * 501 + "English employment requirements",
    "</s>",
    "__label__en",
]
# Current repository regression descriptions.
for node in ast.parse((module.parents[1] / "tests/test_langdetect.py").read_text()).body:
    if (
        isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        texts.append(node.value.value)
phrases = [
    (
        "We are looking for a software engineer to build our products. You will "
        "work with our team and customers. "
    ),
    (
        "Wir suchen eine erfahrene Person für unser Team. Sie entwickeln neue "
        "Produkte und arbeiten mit unseren Kunden zusammen. "
    ),
    (
        "Nous recherchons une personne pour notre équipe. Vous travaillerez avec "
        "nos clients et développerez nos produits. "
    ),
    (
        "Buscamos una persona para nuestro equipo. Desarrollará nuevos productos y "
        "trabajará con nuestros clientes. "
    ),
    (
        "Cerchiamo una persona per il nostro team. Lavorerai con i nostri clienti e "
        "svilupperai nuovi prodotti. "
    ),
    (
        "Wij zoeken een medewerker voor ons team. Je werkt samen met onze klanten "
        "en ontwikkelt nieuwe producten. "
    ),
    (
        "Szukamy pracownika do naszego zespołu. Będziesz pracować z naszymi "
        "klientami i rozwijać nowe produkty. "
    ),
    (
        "Procuramos uma pessoa para nossa equipe. Você trabalhará com nossos "
        "clientes e desenvolverá novos produtos. "
    ),
    (
        "Vi söker en medarbetare till vårt team. Du arbetar med våra kunder och "
        "utvecklar nya produkter. "
    ),
    (
        "Vi søger en medarbejder til vores team. Du arbejder med vores kunder og "
        "udvikler nye produkter. "
    ),
    "Etsimme työntekijää tiimiimme. Työskentelet asiakkaidemme kanssa ja kehität uusia tuotteita. ",
    (
        "Мы ищем сотрудника в нашу команду. Вы будете работать с нашими клиентами и "
        "разрабатывать новые продукты. "
    ),
    "نبحث عن موظف لفريقنا. ستعمل مع عملائنا وتطور منتجات جديدة. ",
    "我们正在寻找一位员工加入我们的团队。您将与我们的客户合作并开发新产品。",
    "私たちのチームに参加する従業員を探しています。お客様と協力し新しい製品を開発します。",
    "우리 팀에 합류할 직원을 찾고 있습니다. 고객과 협력하고 새로운 제품을 개발하게 됩니다. ",
    "हम अपनी टीम के लिए एक कर्मचारी की तलाश कर रहे हैं। आप हमारे ग्राहकों के साथ काम करेंगे। ",
    (
        "Ψάχνουμε έναν εργαζόμενο για την ομάδα μας. Θα συνεργαστείτε με τους "
        "πελάτες μας και θα αναπτύξετε νέα προϊόντα. "
    ),
    "İş arkadaşları arıyoruz. Müşterilerimizle çalışacak ve yeni ürünler geliştireceksiniz. ",
]
for phrase in phrases:
    for transform in [str, str.upper, str.title]:
        p = transform(phrase)
        texts.extend([p, "<p>" + p * 7 + "</p>", "<br>" + p * 3 + "\n" + p * 4])
for a in phrases:
    for b in phrases:
        texts.append(a * 6 + b * 8)
        texts.append(a * 18 + b * 2)
for size in [0, 1, 5, 79, 80, 81, 499, 500, 501, 1000, 3500]:
    for unit in ["x", "字", "Я", "Σ", "\x00", " "]:
        texts.append(unit * size + phrases[0])
for edge in ["\x1c", "\u00a0", "\u200b", "\u2028", "İ", "ΟΣ", "ᾈ", "Ⅰ", "ᴬ", "__label__ru", "</s>"]:
    texts.extend([edge * 90, edge * 6 + phrases[0], phrases[0] + edge * 6])
rng = random.Random(928)
for _ in range(400):
    pieces = rng.choices(
        phrases + ["<strong>", "</strong>", "\n", "😀", "\x00", "</s>"], k=rng.randint(1, 18)
    )
    texts.append("".join(pieces))
cases = []
for text in dict.fromkeys(texts):
    prediction = detect(text, model="lite")
    cases.append(
        {
            "text": text,
            "input": LangDetector._normalize_text(LangDetector()._preprocess_text(text), True),
            "prediction": prediction[0] if prediction else None,
            "language": _detect_language_python(text),
            "languages": _detect_all_languages_python(text),
        }
    )
# Pure text preparation checks: Unicode cased properties, lowercase expansions,
# final-sigma context and Python isupper are independent of classifier scores.
preprocess = []
for cp in range(0x110000):
    c = chr(cp)
    if c.isupper() or c.istitle():
        text = c + " 123"
        preprocess.append([text, LangDetector._normalize_text(text, True)])
result = {
    "model_sha256": hashlib.sha256(model).hexdigest(),
    "max_input_length": 80,
    "cases": cases,
    "preprocess": preprocess,
}
(module / "testdata/python_language.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
)
print(
    f"Frozen {len(cases)} prediction/description cases "
    f"and {len(preprocess)} Unicode preparation cases"
)
