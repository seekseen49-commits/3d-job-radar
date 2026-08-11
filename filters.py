"""Детерминированная классификация 3D-публикаций без AI и внешних API."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Category = Literal["direct_order", "freelance_vacancy", "job_vacancy", "self_promo", "rejected"]
SourceType = Literal["mixed", "job_board"]

POSITIVE = (
    "3д", "blender", "блендер", "моделирован", "смоделир", "визуализац", "рендер", "beauty render",
    "unreal engine", "архвиз", "architectural visualization", "cgi", "stl", "obj", "fbx", "glb", "gltf", "mesh",
    "sculpt", "скульпт", "ретополог", "текстурирован", "3d-печать", "3д-печать", "game ready",
    "environment artist", "character artist", "hard surface", "prop artist", "3d artist", "3d generalist", "modeller", "modeler",
    "cinema 4d", "maya", "3ds max", "zbrush", "substance painter", "product visualization", "предметная визуализация",
)
NEGATIVE = ("видеомонтаж", "монтаж видео", "озвучк", "ai-видео", "ai видео", "нейровидео", "графический дизайн", "логотип", "курсы", "обучение", "вебинар", "новости", "продам", "продажа")
ASSET_PROMO_NEGATIVE = (
    "asset pack", "assets for sale", "buy assets", "download assets",
    "free asset pack", "marketplace", "asset store",
    "пак ассетов", "ассеты на продажу", "купить ассеты", "скачать ассеты",
    "бесплатный пакет ассетов", "маркетплейс", "магазин ассетов",
)
PRIVATE_PROMO_CUES = (
    "меня зовут", "я cg generalist", "я дизайнер", "я художник", "я моделлер", "я 3d-художник", "я 3д-художник",
    "я 3d artist", "i am a 3d artist", "i am a designer", "занимаюсь", "подключаюсь к проект", "беру проект",
    "работаю по тз", "мой опыт", "мои работы", "портфолио", "behance", "artstation", "instagram", "открыт к работе",
    "открыта к работе", "готов к сотрудничеству", "контакты", "#резюме", "#портфолио", "my portfolio", "available for work",
    "looking for work", "looking for projects", "hire me", "ищу работу", "ищу проекты", "ищу заказы", "открыт к предложениям",
    "предоставляю услуги", "предлагаю услуги", "мои услуги", "я 2d/3d", "я 2д/3д",
)
STUDIO_PROMO_CUES = (
    "мы студия", "мы команда", "наше агентство", "ии-студия", "создаем визуал", "создаём визуал", "наши услуги",
    "что мы делаем", "работаем с брендами", "поможем вашему бизнесу", "напишите нам", "пришлем варианты",
    "пришлём варианты", "заказать у нас", "предоставляем услуги",
)
PLATFORM_PROMO_CUES = (
    "calling all artists", "calling all 3d artists", "apply now", "join our platform", "find your next project",
    "talented artists like you", "opportunities for artists", "your next project is one click away", "one click away",
)
HIRING_PATTERNS = (
    r"\bищ(?:у|ем)\s+(?:[\w-]+\s+){0,2}[\w-]*(?:специалист|исполнител|моделлер|аниматор|художник|дженералист|команд)[\w-]*",
    r"\bнуж(?:ен|на|ны)\s+(?:[\w-]+\s+){0,2}[\w-]*(?:специалист|исполнител|моделлер|аниматор|художник|дженералист)[\w-]*",
    r"\bтребуется\s+(?:исполнител|моделлер|аниматор|художник)", r"\bкто может сделать", r"\bищем команду",
    r"\bоткликнитесь", r"\bпишите с примерами работ", r"\blooking for", r"\bneed\s+(?:a|an)\s+(?:[\w-]+\s+){0,2}(?:artist|modeler|modeller|animator)", r"\bhiring\b", r"\bseeking a contractor",
    r"\bнужно смоделировать", r"\bтребуется подготовить",
)
DELIVERABLE_PATTERNS = (
    r"\bнужно сделать", r"\bнужно смоделировать", r"\bнеобходимо смоделир", r"\bтребуется создать",
    r"\bтребуется подготовить", r"\bнадо подготовить", r"\bзадача состоит в", r"\bсделать\s+\d+\s+(?:модел|рендер|ролик|объект)",
    r"\b(?:3d[- ]?)?model\b", r"\b(?:beauty )?render\b", r"\buv\s*(?:unwrapping|[-–—]развертк)",
    r"\b(?:product|предметн\w*)\s+(?:3d[- ]?)?model\b", r"\b(?:ролик|stl[- ]?модел)\b", r"\bдля создан",
)
FREELANCE_VACANCY_CUES = ("freelance", "contract", "project-based", "part-time", "контракт", "проектная роль", "проектная позиция")
JOB_VACANCY_CUES = ("ваканси", "компания ищет", "full-time", "employment", "office", "relocation", "обязанност", "требовани", "условия", "отправить резюме", "резюме на", "штат", "команда", "оформление", "испытательн", "зарплата", "заработная плат", "в месяц", "в год", "per month", "per year")
ROLE_CUES = ("senior", "lead", "middle", "junior", "art director", "3d artist", "3d generalist", "environment artist", "prop artist", "motion designer", "modeler", "modeller")
CURRENCY = r"(?:₽|руб(?:\.|лей|ля)?|р\.|rub\b|\$|usd\b|€|eur\b|тенге|тг\b|₸)"
PRICE_RE = re.compile(rf"(?P<a>\d[\d\s]*)(?:\s*[-–—]\s*(?P<b>\d[\d\s]*))?\s*{CURRENCY}", re.IGNORECASE)
CONTEXT_PRICE_RE = re.compile(r"(?:бюджет|оплата|цена|гонорар)\D{0,20}(?P<a>\d[\d\s]*)(?:\s*[-–—]\s*(?P<b>\d[\d\s]*))?", re.IGNORECASE)
SHORT_TECHNICAL_PATTERNS = {
    "uv": r"(?<!\w)uv(?!\w)", "ue": r"(?<!\w)ue\d*(?!\w)", "ai": r"(?<!\w)ai(?!\w)",
    "3d": r"(?<!\w)3d(?!\w)", "2d": r"(?<!\w)2d(?!\w)", "cad": r"(?<!\w)cad(?!\w)",
    "c4d": r"(?<!\w)c4d(?!\w)", "rig": r"(?<!\w)rig(?!\w)", "vr": r"(?<!\w)vr(?!\w)",
    "ar": r"(?<!\w)ar(?:kit)?(?!\w)",
}
THREE_D_SHORT_KEYS = frozenset({"uv", "ue", "3d", "cad", "c4d", "rig", "vr", "ar"})


@dataclass(frozen=True)
class FilterResult:
    category: Category
    reason: str
    price: str
    hiring_intent_matches: tuple[str, ...] = field(default_factory=tuple)
    deliverable_matches: tuple[str, ...] = field(default_factory=tuple)
    self_promo_matches: tuple[str, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> bool:
        return self.category in {"direct_order", "freelance_vacancy", "job_vacancy"}


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def _matches(content: str, patterns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(pattern for pattern in patterns if re.search(pattern, content, re.IGNORECASE))


def _cue_matches(content: str, cues: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(cue for cue in cues if cue in content)


def short_technical_matches(text: str) -> tuple[str, ...]:
    """Ищет короткие технические ключи только как самостоятельные токены."""
    content = normalize(text)
    return tuple(key for key, pattern in SHORT_TECHNICAL_PATTERNS.items() if re.search(pattern, content, re.IGNORECASE))


def positive_matches(text: str) -> tuple[str, ...]:
    content = normalize(text)
    long_matches = _cue_matches(content, POSITIVE)
    short_matches = tuple(key for key in short_technical_matches(text) if key in THREE_D_SHORT_KEYS)
    return long_matches + short_matches


def extract_price(text: str) -> tuple[int | None, int | None, str]:
    match = PRICE_RE.search(text) or CONTEXT_PRICE_RE.search(text)
    if not match: return None, None, "не указана"
    raw = match.group(0)
    if not re.search(r"₽|руб|\bр\.|\brub\b", raw, re.IGNORECASE): return None, None, raw.strip()
    low = int(match.group("a").replace(" ", ""))
    high = int(match.group("b").replace(" ", "")) if match.group("b") else None
    if high is None:
        high = None if re.search(r"\bот\s*$", text[max(0, match.start() - 20):match.start()].lower()) else low
    return low, high, raw.strip()


def price_is_acceptable(text: str) -> tuple[bool, str]:
    _, high, label = extract_price(text)
    return high is None or high >= 1000, label


def _result(category: Category, reason: str, price: str, hiring: tuple[str, ...], deliverable: tuple[str, ...], promo: tuple[str, ...]) -> FilterResult:
    return FilterResult(category, reason, price, hiring, deliverable, promo)


def evaluate(text: str, mode: str, source_type: SourceType = "mixed") -> FilterResult:
    """Direct order требует явного найма/результата и отсутствия саморекламы."""
    content = normalize(text)
    price = extract_price(text)[2]
    hiring = _matches(content, HIRING_PATTERNS)
    deliverable = _matches(content, DELIVERABLE_PATTERNS)
    promo = _cue_matches(content, PRIVATE_PROMO_CUES) + _cue_matches(content, STUDIO_PROMO_CUES)
    if mode not in {"general", "3d_only"} or source_type not in {"mixed", "job_board"}:
        return _result("rejected", "неизвестный режим источника", price, hiring, deliverable, promo)
    # Самореклама имеет приоритет над общими словами задачи, ТЗ, сроков и стоимости.
    if promo:
        return _result("self_promo", f"доминирующая самопрезентация ({len(promo)}): {', '.join(promo)}", price, hiring, deliverable, promo)
    platform_promo = _cue_matches(content, PLATFORM_PROMO_CUES)
    if platform_promo:
        return _result("rejected", "реклама платформы или сервиса", price, hiring, deliverable, promo)
    negative = _cue_matches(content, NEGATIVE) + _cue_matches(content, ASSET_PROMO_NEGATIVE)
    if negative:
        return _result("rejected", f"исключено: {negative[0]}", price, hiring, deliverable, promo)
    if not content and mode != "3d_only":
        return _result("rejected", "в общем канале нет текста с признаками 3D", price, hiring, deliverable, promo)
    has_3d = mode == "3d_only" or bool(positive_matches(text))
    # Вакансии и заказы допустимы лишь после обязательной проверки реальной 3D-релевантности.
    if not has_3d:
        return _result("rejected", "нет настоящих признаков 3D", price, hiring, deliverable, promo)
    price_ok, price = price_is_acceptable(text)
    if not price_ok:
        return _result("rejected", f"максимальная цена ниже 1000 ₽ ({price})", price, hiring, deliverable, promo)

    freelance = _cue_matches(content, FREELANCE_VACANCY_CUES)
    job = _cue_matches(content, JOB_VACANCY_CUES)
    role = _cue_matches(content, ROLE_CUES)
    if job and (source_type == "job_board" or role):
        return _result("job_vacancy", f"признак штатной вакансии: {job[0]}", price, hiring, deliverable, promo)
    if freelance and (source_type == "job_board" or role):
        return _result("freelance_vacancy", f"признак контрактной роли: {freelance[0]}", price, hiring, deliverable, promo)
    # На job_board заголовок роли без результата остаётся вакансией, а не прямым заказом.
    if source_type == "job_board" and (job or freelance or role) and not deliverable:
        category: Category = "freelance_vacancy" if freelance else "job_vacancy"
        return _result(category, f"заголовок вакансии в job_board: {(freelance or job or role)[0]}", price, hiring, deliverable, promo)
    if has_3d and hiring and deliverable:
        labels = [f"hiring_intent_matches={len(hiring)}", f"deliverable_matches={len(deliverable)}", "self_promo_matches=0"]
        return _result("direct_order", "явная разовая задача; " + ", ".join(labels), price, hiring, deliverable, promo)
    return _result("rejected", "нет намерения нанять или описания конкретного результата", price, hiring, deliverable, promo)
