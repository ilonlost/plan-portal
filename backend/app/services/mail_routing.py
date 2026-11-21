from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import PortalSetting
from app.services.settings_service import get_planner_configuration


@dataclass(frozen=True)
class MailAction:
    group: str
    title: str
    content: str


ACTIONS = {
    "manual_task_created": MailAction("planning", "Создание ручного задания", "План, SKU, линия, дата, объём и автор задания."),
    "schedule_item_updated": MailAction("planning", "Изменение задания", "Номер задания, план, линия, дата и автор изменения."),
    "schedule_event_created": MailAction("planning", "Добавление события линии", "Вид события, линия, дата, время, длительность и причина."),
    "schedule_item_deleted": MailAction("planning", "Исключение задания", "Номер исключённого задания, план и автор изменения."),
    "schedule_items_bulk_deleted": MailAction("planning", "Исключение заданий за период", "Период, количество заданий, выбранная линия и учёт ручных заданий."),
    "plan_approved": MailAction("planning", "Утверждение плана", "Название плана, кто его утвердил и комментарий."),
    "shipment_not_shipped": MailAction("planning", "Отгрузка не состоялась", "Продукт, линия, статус, причина и кто сообщил об отклонении."),
    "shipment_partially_shipped": MailAction("planning", "Частичная отгрузка", "Продукт, линия, статус, причина и кто сообщил об отклонении."),
    "line_comment_added": MailAction("planning", "Добавление комментария к линии", "Линия, дата, автор и текст комментария."),
    "line_actual_attendance_saved": MailAction("planning", "Сохранение фактической явки", "Цех, линия, дата, число вышедших сотрудников и кто заполнил явку."),
    "plan_imported": MailAction("production_management", "Загрузка спроса в план", "Название файла, источник, количество строк и кто загрузил файл."),
    "plan_imported_line": MailAction("production_management", "Загрузка спроса на одну линию", "Линия, файл, количество строк и кто загрузил файл."),
    "reference_imported": MailAction("production_management", "Загрузка справочника через импорт", "Имя файла, число обработанных строк и кто выполнил загрузку."),
    "catalog_imported": MailAction("production_management", "Импорт Excel в справочнике", "Имя файла, количество добавленных и обновлённых строк."),
    "catalog_product_created": MailAction("production_management", "Создание артикула", "SKU, название продукции, автор и результат пересчёта плана."),
    "catalog_product_updated": MailAction("production_management", "Изменение артикула", "SKU, название, список изменённых полей и результат пересчёта."),
    "catalog_product_blocked": MailAction("production_management", "Блокировка артикула", "SKU, новый статус, автор и результат пересчёта плана."),
    "catalog_product_active": MailAction("production_management", "Активация артикула", "SKU, новый статус, автор и результат пересчёта плана."),
    "catalog_product_deleted": MailAction("production_management", "Удаление артикула", "SKU исключённой продукции, автор и результат пересчёта."),
    "catalog_product_restored": MailAction("production_management", "Восстановление артикула", "SKU, название возвращённой продукции и результат пересчёта."),
    "production_parameters_updated": MailAction("production_management", "Изменение параметров SKU на линии", "SKU, линия, изменённые поля и результат пересчёта."),
    "line_schedule_updated": MailAction("production_management", "Изменение настроек линии", "Линия, автор изменения графика или мощности и результат пересчёта."),
    "csb_next_day_prepared": MailAction("production_management", "Подготовка задания CSB", "Дата, план, число позиций, автор и отметка тестового режима."),
    "feedback_created": MailAction("it", "Создание обращения", "Тема, тип, номер, автор, его почта и текст обращения."),
    "user_access_created": MailAction("it", "Добавление пользователя через управление доступом", "Логин, назначенная роль и кто выдал доступ."),
    "user_access_updated": MailAction("it", "Изменение доступа пользователя", "Логин сотрудника и кто изменил его права."),
    "user_access_deleted": MailAction("it", "Удаление пользователя", "Логин удалённого пользователя и кто выполнил удаление."),
    "mail_configuration_updated": MailAction("it", "Изменение настроек почты", "Кто изменил настройки SMTP, шаблон или правило рассылки."),
    "portal_configuration_updated": MailAction("it", "Изменение настроек портала", "Кто изменил параметры простоев или видимость разделов."),
    "plan_data_deleted": MailAction("it", "Очистка всех планов", "Кто выполнил очистку и сколько данных удалено."),
    "plan_email_production": MailAction("manual", "Отправка плана производству", "Задания по дням и линиям, смены, объёмы и время. План в XLSX."),
    "plan_email_warehouse": MailAction("manual", "Отправка плана складу", "Даты отгрузки, SKU, килограммы, короба и Меркурий. План в XLSX."),
    "plan_email_materials": MailAction("manual", "Отправка плана сырьевому отделу", "Потребность в готовой продукции по SKU в штуках, килограммах и коробах. План в XLSX."),
}

RULE_PREFIX = "mail_action."
INHERIT = {"mode": "inherit", "recipients": ""}


def action_rule(db: Session, action: str) -> dict:
    return db.scalar(select(PortalSetting.value).where(PortalSetting.key == RULE_PREFIX + action)) or INHERIT


def addresses(raw: str, extra: list[str] | None = None) -> list[str]:
    values = [value.strip().lower() for value in [*(raw or "").split(","), *(extra or [])] if value]
    return list(dict.fromkeys(value for value in values if "@" in value))


def inherited_addresses(configuration: dict, action: str, extra: list[str] | None = None) -> list[str]:
    definition = ACTIONS.get(action)
    group = definition.group if definition else None
    raw = (configuration.get("event_recipients") or {}).get(group) or configuration.get("notification_emails", "")
    if action == "feedback_created":
        extra = [*re.split(r"[;,]", settings.feedback_emails), *(extra or [])]
    return addresses(raw, extra)


def routed_addresses(rule: dict, inherited: list[str]) -> list[str]:
    if rule["mode"] == "off":
        return []
    if rule["mode"] == "custom":
        return addresses(rule["recipients"])
    return inherited


def mail_actions(db: Session, configuration: dict) -> list[dict]:
    rules = dict(db.execute(select(PortalSetting.key, PortalSetting.value).where(
        PortalSetting.key.startswith(RULE_PREFIX, autoescape=True)
    )).all())
    audiences = get_planner_configuration(db)["recipients"]
    return [
        {"key": key, "group": action.group, "title": action.title, "content": action.content,
         "rule": rules.get(RULE_PREFIX + key, INHERIT),
         "inherited": addresses(audiences[key.removeprefix("plan_email_")]) if action.group == "manual"
         else inherited_addresses(configuration, key)}
        for key, action in ACTIONS.items()
    ]
