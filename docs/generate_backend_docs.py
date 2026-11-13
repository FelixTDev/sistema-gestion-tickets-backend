# ruff: noqa: E501
from __future__ import annotations

import ast
import html
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUTPUT = ROOT / "output" / "pdf"
MD_PATH = DOCS / "DOCUMENTACION_OFICIAL_BACKEND.md"
PDF_PATH = OUTPUT / "DOCUMENTACION_OFICIAL_BACKEND.pdf"


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    function: str
    response: str
    status: str
    file: str
    line: int


@dataclass(frozen=True)
class RouteMeta:
    description: str
    auth: str
    role: str
    request: str
    response: str
    errors: str


def _unparse(value: ast.AST | None) -> str | None:
    return ast.unparse(value) if value is not None else None


def extract_routes() -> list[Route]:
    routes: list[Route] = []
    for path in sorted((ROOT / "app").glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        prefixes: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if (
                not isinstance(node.value.func, ast.Name)
                or node.value.func.id != "APIRouter"
            ):
                continue
            prefix = ""
            for keyword in node.value.keywords:
                if keyword.arg == "prefix" and isinstance(keyword.value, ast.Constant):
                    prefix = str(keyword.value.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = prefix
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Attribute):
                    continue
                if not isinstance(decorator.func.value, ast.Name):
                    continue
                router_name = decorator.func.value.id
                if router_name not in prefixes:
                    continue
                route_path = (
                    ast.literal_eval(decorator.args[0])
                    if decorator.args and isinstance(decorator.args[0], ast.Constant)
                    else ""
                )
                keywords = {
                    item.arg: item.value for item in decorator.keywords if item.arg
                }
                response = _unparse(keywords.get("response_model")) or "Response"
                status = _unparse(keywords.get("status_code")) or "200"
                router_prefix = prefixes[router_name]
                full_path = (
                    f"{router_prefix}{route_path}"
                    if router_prefix.startswith("/api/v1")
                    else f"/api/v1{router_prefix}{route_path}"
                ) or "/"
                routes.append(
                    Route(
                        method=decorator.func.attr.upper(),
                        path=full_path,
                        function=node.name,
                        response=response,
                        status=status,
                        file=str(path.relative_to(ROOT)).replace("\\", "/"),
                        line=node.lineno,
                    )
                )
    return sorted(routes, key=lambda item: (item.path, item.method))


def extract_model_inventory() -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in sorted((ROOT / "app/modules").glob("**/models/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("SQLModel" in (_unparse(base) or "") for base in node.bases):
                continue
            table_name = None
            fields: list[str] = []
            foreign_keys: list[str] = []
            for item in node.body:
                if isinstance(item, ast.Assign):
                    names = [
                        target.id
                        for target in item.targets
                        if isinstance(target, ast.Name)
                    ]
                    if "__tablename__" in names and isinstance(
                        item.value, ast.Constant
                    ):
                        table_name = str(item.value.value)
                if isinstance(item, ast.AnnAssign) and isinstance(
                    item.target, ast.Name
                ):
                    name = item.target.id
                    if name.startswith("_"):
                        continue
                    annotation = _unparse(item.annotation) or "object"
                    fields.append(f"{name}: {annotation}")
                    value = _unparse(item.value) or ""
                    match = re.search(r"foreign_key=['\"]([^'\"]+)", value)
                    if match:
                        foreign_keys.append(f"{name} -> {match.group(1)}")
                    match = re.search(r"ForeignKey\(['\"]([^'\"]+)", value)
                    if match:
                        foreign_keys.append(f"{name} -> {match.group(1)}")
            if table_name:
                inventory.append(
                    {
                        "name": node.name,
                        "table": table_name,
                        "fields": fields,
                        "foreign_keys": sorted(set(foreign_keys)),
                        "file": str(path.relative_to(ROOT)).replace("\\", "/"),
                    }
                )
    return sorted(inventory, key=lambda item: item["table"])


def count_files(pattern: str, directory: Path) -> int:
    return sum(1 for path in directory.glob(pattern) if path.is_file())


def route_key(route: Route) -> str:
    return f"{route.method} {route.path}"


COMMON_AUTH = "Bearer JWT"
ANY_ROLE = "CLIENTE, ASESOR o SUPERVISOR"
STAFF = "ASESOR o SUPERVISOR"
SUPERVISOR = "SUPERVISOR"


META: dict[str, RouteMeta] = {
    "GET /api/v1/health": RouteMeta(
        "Comprobación estática de disponibilidad de la API.",
        "No",
        "-",
        "Sin body",
        "{status, service}",
        "-",
    ),
    "POST /api/v1/auth/register": RouteMeta(
        "Registra una cuenta nueva y siempre asigna el rol CLIENTE.",
        "No",
        "-",
        "RegisterRequest: full_name, email, password, phone?",
        "PublicUser",
        "409 correo duplicado; 422 validación",
    ),
    "POST /api/v1/auth/login": RouteMeta(
        "Valida credenciales y emite un access token JWT vinculado a una sesión persistente.",
        "No",
        "-",
        "LoginRequest: email, password",
        "LoginResponse",
        "401 credenciales inválidas o rate limit; 422 validación",
    ),
    "GET /api/v1/auth/me": RouteMeta(
        "Devuelve la identidad pública del usuario autenticado.",
        COMMON_AUTH,
        ANY_ROLE,
        "Sin body",
        "PublicUser",
        "401 token ausente, inválido, expirado o revocado",
    ),
    "POST /api/v1/auth/logout": RouteMeta(
        "Revoca la sesión asociada al JWT actual.",
        COMMON_AUTH,
        ANY_ROLE,
        "Sin body",
        "LogoutResponse",
        "401 token inválido",
    ),
    "POST /api/v1/auth/forgot-password": RouteMeta(
        "Solicita recuperación sin revelar si el correo existe.",
        "No",
        "-",
        "ForgotPasswordRequest: email",
        "MessageResponse (202)",
        "202 respuesta uniforme; 422 validación",
    ),
    "POST /api/v1/auth/reset-password": RouteMeta(
        "Consume un token de recuperación de un solo uso y revoca sesiones previas.",
        "No",
        "-",
        "ResetPasswordRequest: token, new_password",
        "MessageResponse",
        "400 token inválido o usado; 429 rate limit; 422 validación",
    ),
    "POST /api/v1/auth/change-password": RouteMeta(
        "Cambia la contraseña con la contraseña actual y revoca sesiones anteriores.",
        COMMON_AUTH,
        ANY_ROLE,
        "ChangePasswordRequest: current_password, new_password",
        "MessageResponse",
        "400 contraseña actual inválida; 401 autenticación; 422 validación",
    ),
    "POST /api/v1/auth/verify-email": RouteMeta(
        "Consume un token de verificación de correo de un solo uso.",
        "No",
        "-",
        "VerifyEmailRequest: token",
        "MessageResponse",
        "400 token inválido o usado; 429 rate limit; 422 validación",
    ),
    "GET /api/v1/users/advisors": RouteMeta(
        "Lista asesores activos para selección operativa.",
        COMMON_AUTH,
        SUPERVISOR,
        "Sin body",
        "list[PublicUser]",
        "401/403 autenticación o rol; 422 validación",
    ),
    "GET /api/v1/users/me/profile": RouteMeta(
        "Consulta el perfil público ampliado del usuario actual.",
        COMMON_AUTH,
        ANY_ROLE,
        "Sin body",
        "ProfileRead",
        "401 token inválido",
    ),
    "PATCH /api/v1/users/me/profile": RouteMeta(
        "Actualiza únicamente full_name y phone del propio usuario.",
        COMMON_AUTH,
        ANY_ROLE,
        "ProfileUpdate: full_name?, phone?",
        "ProfileRead",
        "401 autenticación; 422 payload o teléfono inválido",
    ),
    "GET /api/v1/users/me/preferences": RouteMeta(
        "Obtiene o crea preferencias persistentes del usuario.",
        COMMON_AUTH,
        ANY_ROLE,
        "Sin body",
        "PreferencesRead",
        "401 token inválido",
    ),
    "PATCH /api/v1/users/me/preferences": RouteMeta(
        "Actualiza preferencias de notificación, idioma y zona horaria.",
        COMMON_AUTH,
        ANY_ROLE,
        "PreferencesUpdate: flags?, preferred_language?, timezone?",
        "PreferencesRead",
        "401 autenticación; 422 zona horaria o seguridad desactivada",
    ),
    "GET /api/v1/faqs": RouteMeta(
        "Lista FAQs publicadas, activas, vigentes y de categorías activas; soporta filtros y paginación opcional.",
        "No",
        "-",
        "Query: search?, category_id?, tag?, published_from?, published_to?, page?, page_size?",
        "list[FAQRead] o FAQPage",
        "422 parámetros o rango de fechas inválido",
    ),
    "GET /api/v1/faqs/admin": RouteMeta(
        "Lista el catálogo editorial con filtros de estado, contenido y fechas.",
        COMMON_AUTH,
        SUPERVISOR,
        "Query: status?, search?, category_id?, tag?, created_from?, created_to?, updated_from?, updated_to?, page=1, page_size=20",
        "FAQAdminPage",
        "401/403; 422 filtros o rangos",
    ),
    "GET /api/v1/faqs/admin/metrics/utility": RouteMeta(
        "Devuelve métricas agregadas de utilidad del feedback de FAQs.",
        COMMON_AUTH,
        SUPERVISOR,
        "Sin body",
        "FAQUtilityMetrics",
        "401/403",
    ),
    "GET /api/v1/faqs/{faq_id}/history": RouteMeta(
        "Consulta snapshots editoriales de una FAQ.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: faq_id",
        "list[FAQVersionRead]",
        "401/403; 404 FAQ inexistente",
    ),
    "POST /api/v1/faqs/{faq_id}/feedback": RouteMeta(
        "Registra feedback anónimo o de un cliente sobre una FAQ activa.",
        "Opcional",
        "Visitante o CLIENTE",
        "Path: faq_id; FAQFeedbackCreate: is_helpful, comment?",
        "FAQFeedbackRead (201)",
        "403 rol distinto de CLIENTE; 404 FAQ; 422 validación",
    ),
    "GET /api/v1/faqs/{faq_id}": RouteMeta(
        "Obtiene una FAQ pública activa.",
        "No",
        "-",
        "Path: faq_id",
        "FAQRead",
        "404 FAQ inexistente o no pública",
    ),
    "POST /api/v1/faqs": RouteMeta(
        "Crea contenido de conocimiento como DRAFT inactivo y registra su primera versión.",
        COMMON_AUTH,
        SUPERVISOR,
        "FAQCreate: category_id, question, answer, keywords, title?, summary?, tags?, synonyms?, intent?, priority?, display_order?",
        "FAQRead (201)",
        "401/403; 409 contenido duplicado; 422 categoría o validación",
    ),
    "PATCH /api/v1/faqs/{faq_id}": RouteMeta(
        "Actualiza una FAQ; si una FAQ publicada cambia contenido vuelve a REVIEW e incrementa versión.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: faq_id; FAQUpdate parcial",
        "FAQRead",
        "401/403; 404; 409 duplicado; 422 payload o categoría",
    ),
    "PATCH /api/v1/faqs/{faq_id}/workflow": RouteMeta(
        "Aplica transiciones editoriales DRAFT, REVIEW, PUBLISHED o ARCHIVED.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: faq_id; WorkflowStatusUpdate: status",
        "FAQRead",
        "401/403; 404; 409 transición inválida; 422 publicación no válida",
    ),
    "PATCH /api/v1/faqs/{faq_id}/status": RouteMeta(
        "Atajo para publicar o archivar una FAQ mediante is_active.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: faq_id; ActiveStatusUpdate: is_active",
        "FAQRead",
        "401/403; 404; 409 transición; 422 contenido/categoría",
    ),
    "GET /api/v1/categories": RouteMeta(
        "Lista categorías activas.", "No", "-", "Sin body", "list[CategoryRead]", "-"
    ),
    "POST /api/v1/categories": RouteMeta(
        "Crea una categoría de tickets.",
        COMMON_AUTH,
        SUPERVISOR,
        "CategoryCreate: name, description",
        "CategoryRead (201)",
        "401/403; 409 nombre duplicado; 422 validación",
    ),
    "PATCH /api/v1/categories/{category_id}": RouteMeta(
        "Actualiza nombre o descripción de una categoría.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: category_id; CategoryUpdate: name?, description?",
        "CategoryRead",
        "401/403; 404; 409 nombre duplicado; 422 validación",
    ),
    "PATCH /api/v1/categories/{category_id}/status": RouteMeta(
        "Activa o desactiva una categoría; las categorías usadas por tickets no pueden desactivarse.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: category_id; ActiveStatusUpdate: is_active",
        "CategoryRead",
        "401/403; 404; 409 categoría usada; 422 validación",
    ),
    "POST /api/v1/chat/conversations": RouteMeta(
        "Crea una conversación anónima o asociada al cliente autenticado.",
        "Opcional",
        "Visitante o CLIENTE",
        "Sin body",
        "ConversationRead (201)",
        "403 ASESOR/SUPERVISOR; 429 cuota anónima",
    ),
    "GET /api/v1/chat/conversations/{conversation_id}": RouteMeta(
        "Devuelve conversación y mensajes si el visitante o cliente está autorizado.",
        "Opcional",
        "Visitante de anónima o CLIENTE propietario",
        "Path: conversation_id",
        "ConversationRead",
        "401 conversación vinculada; 403; 404",
    ),
    "POST /api/v1/chat/conversations/{conversation_id}/messages": RouteMeta(
        "Procesa un mensaje mediante recuperación FAQ determinista y fallback/IA opcional.",
        "Opcional",
        "Visitante o CLIENTE",
        "Path: conversation_id; MessageCreate: content",
        "BotMessageResponse",
        "403; 404; 409 conversación terminal; 422 contenido; 429 límites",
    ),
    "POST /api/v1/chat/conversations/{conversation_id}/escalate": RouteMeta(
        "Deriva una conversación a atención humana de forma idempotente.",
        "Opcional",
        "Visitante o CLIENTE",
        "Path: conversation_id; ConversationActionRequest: reason?",
        "ConversationRead",
        "403; 404; 409 ya convertida; 422 validación",
    ),
    "POST /api/v1/chat/conversations/{conversation_id}/reset": RouteMeta(
        "Reinicia el contexto operativo sin borrar mensajes históricos.",
        "Opcional",
        "Visitante o CLIENTE",
        "Path: conversation_id",
        "ConversationRead",
        "403; 404; 409 ya convertida",
    ),
    "POST /api/v1/chat/conversations/{conversation_id}/feedback": RouteMeta(
        "Registra utilidad y aceptación de escalamiento sobre la última FAQ usada.",
        "Opcional",
        "Visitante o CLIENTE",
        "Path: conversation_id; ChatFeedbackCreate: is_helpful, escalation_accepted?, reason?",
        "ChatFeedbackRead (201)",
        "403; 404; 422 sin FAQ evaluable o validación",
    ),
    "POST /api/v1/chat/conversations/{conversation_id}/link-user": RouteMeta(
        "Asocia una conversación anónima con el cliente autenticado.",
        COMMON_AUTH,
        "CLIENTE",
        "Path: conversation_id",
        "LinkConversationResponse",
        "401; 403 rol o conversación; 404",
    ),
    "POST /api/v1/chat/conversations/{conversation_id}/convert-to-ticket": RouteMeta(
        "Convierte una conversación autenticada no resuelta en un ticket único.",
        COMMON_AUTH,
        "CLIENTE",
        "Path: conversation_id; TicketCreate",
        "TicketRead (201)",
        "401; 403; 404; 409 estado/duplicado/no resuelta; 422 categoría; 429 cuota diaria",
    ),
    "POST /api/v1/tickets": RouteMeta(
        "Crea ticket manual con código TCK, prioridad y estado NUEVO; inicia SLA e historial.",
        COMMON_AUTH,
        "CLIENTE",
        "TicketCreate: category_id, subject, description, priority",
        "TicketRead (201)",
        "401/403; 422 categoría o payload",
    ),
    "GET /api/v1/tickets/mine": RouteMeta(
        "Lista únicamente los tickets del cliente actual; modo legacy sin paginación o modo paginado.",
        COMMON_AUTH,
        "CLIENTE",
        "Query: status?, category_id?, priority?, created_from?, created_to?, source?, assigned_advisor_id?, search?, updated_from?, updated_to?, page?, page_size?",
        "list[TicketRead] o TicketPage",
        "401/403; 422 filtros/rangos",
    ),
    "GET /api/v1/tickets": RouteMeta(
        "Lista tickets para personal interno; los asesores quedan limitados a tickets asignados.",
        COMMON_AUTH,
        STAFF,
        "Query: status?, category_id?, priority?, created_from?, created_to?, source?, client_id?, assigned_advisor_id?, search?, updated_from?, updated_to?, page?, page_size?",
        "list[TicketRead] o TicketPage",
        "401/403; 422 filtros/rangos",
    ),
    "GET /api/v1/tickets/operations": RouteMeta(
        "Consulta bandejas operativas paginadas por cola, SLA, asignación o actividad reciente.",
        COMMON_AUTH,
        STAFF,
        "Query: queue requerido; filtros de ticket; advisor_id?, recent_hours=24, page=1, page_size=20",
        "TicketPage",
        "401/403; 422 cola, asesor, rangos o recent_hours",
    ),
    "GET /api/v1/tickets/{ticket_id}": RouteMeta(
        "Obtiene un ticket si el cliente es propietario, el asesor está asignado o el actor es supervisor.",
        COMMON_AUTH,
        ANY_ROLE,
        "Path: ticket_id",
        "TicketRead",
        "401; 403 no autorizado; 404",
    ),
    "POST /api/v1/tickets/{ticket_id}/comments": RouteMeta(
        "Agrega comentario, historial, primera respuesta SLA y notificación al interlocutor.",
        COMMON_AUTH,
        "Propietario CLIENTE, asesor asignado o SUPERVISOR",
        "Path: ticket_id; CommentCreate: content, expected_version?",
        "CommentRead (201)",
        "401/403; 404; 409 cerrado/cancelado o versión; 422 contenido",
    ),
    "GET /api/v1/tickets/{ticket_id}/comments": RouteMeta(
        "Lista comentarios autorizados en orden de creación.",
        COMMON_AUTH,
        ANY_ROLE,
        "Path: ticket_id",
        "list[CommentRead]",
        "401; 403; 404",
    ),
    "POST /api/v1/tickets/{ticket_id}/status": RouteMeta(
        "Cambia el estado con la matriz de transiciones válida y registra historial/notificación.",
        COMMON_AUTH,
        STAFF,
        "Path: ticket_id; StatusChange: status, reason?, expected_version?",
        "TicketRead",
        "401/403; 404; 409 transición, terminal o versión; 422 motivo/payload",
    ),
    "POST /api/v1/tickets/{ticket_id}/assignments": RouteMeta(
        "Asigna o reasigna un ticket a un asesor activo.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: ticket_id; AssignmentCreate: advisor_id, expected_version?",
        "TicketRead (201)",
        "401/403; 404; 409 duplicado/terminal/versión; 422 asesor o payload",
    ),
    "POST /api/v1/tickets/{ticket_id}/take": RouteMeta(
        "Permite a un asesor tomar un ticket abierto sin asignación.",
        COMMON_AUTH,
        "ASESOR",
        "Path: ticket_id; TicketActionRequest opcional: expected_version?",
        "TicketRead",
        "401/403; 404; 409 asignado/terminal/versión",
    ),
    "POST /api/v1/tickets/{ticket_id}/release": RouteMeta(
        "Libera asignación propia del asesor o una asignación que gestiona un supervisor.",
        COMMON_AUTH,
        STAFF,
        "Path: ticket_id; TicketActionRequest opcional: expected_version?",
        "TicketRead",
        "401/403; 404; 409 sin asignación/terminal/versión",
    ),
    "GET /api/v1/tickets/{ticket_id}/history": RouteMeta(
        "Devuelve el historial de mutaciones del ticket.",
        COMMON_AUTH,
        "CLIENTE/ASESOR/SUPERVISOR con autorización sobre el ticket",
        "Path: ticket_id",
        "list[HistoryRead]",
        "401; 403; 404",
    ),
    "POST /api/v1/tickets/{ticket_id}/close": RouteMeta(
        "Cierra un ticket mediante una transición válida.",
        COMMON_AUTH,
        STAFF,
        "Path: ticket_id",
        "TicketRead",
        "401/403; 404; 409 transición/terminal",
    ),
    "POST /api/v1/tickets/{ticket_id}/reopen": RouteMeta(
        "Reabre un ticket RESUELTO hacia EN_PROCESO y exige motivo.",
        COMMON_AUTH,
        "ASESOR con asignación o SUPERVISOR",
        "Path: ticket_id; ReasonRequest: reason, expected_version?",
        "TicketRead",
        "401/403; 404; 409 estado/versión; 422 motivo",
    ),
    "POST /api/v1/tickets/{ticket_id}/cancel": RouteMeta(
        "Cancela un ticket con motivo; la operación está reservada al supervisor.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: ticket_id; ReasonRequest: reason, expected_version?",
        "TicketRead",
        "401/403; 404; 409 transición/terminal/versión; 422 motivo",
    ),
    "GET /api/v1/tickets/{ticket_id}/sla": RouteMeta(
        "Consulta el registro SLA y la política aplicada al ticket autorizado.",
        COMMON_AUTH,
        "CLIENTE/ASESOR/SUPERVISOR con autorización sobre el ticket",
        "Path: ticket_id",
        "TicketSlaRead",
        "401; 403; 404; 422 política inexistente",
    ),
    "POST /api/v1/tickets/{ticket_id}/sla/pause": RouteMeta(
        "Pausa manualmente un SLA activo con motivo e historial.",
        COMMON_AUTH,
        "ASESOR o SUPERVISOR con acceso al ticket",
        "Path: ticket_id; SlaActionRequest: reason",
        "TicketSlaRead",
        "401/403; 404; 409 SLA inactivo/ticket terminal; 422 motivo",
    ),
    "POST /api/v1/tickets/{ticket_id}/sla/resume": RouteMeta(
        "Reanuda SLA pausado y extiende vencimientos por el tiempo pausado.",
        COMMON_AUTH,
        "ASESOR o SUPERVISOR con acceso al ticket",
        "Path: ticket_id",
        "TicketSlaRead",
        "401/403; 404; 409 SLA no pausado; 422 reloj inválido",
    ),
    "POST /api/v1/sla/evaluate": RouteMeta(
        "Evalúa bajo demanda advertencias y vencimientos de SLA idempotentemente.",
        COMMON_AUTH,
        SUPERVISOR,
        "Sin body",
        "SlaEvaluationRead",
        "401/403",
    ),
    "GET /api/v1/sla/policies": RouteMeta(
        "Lista políticas SLA persistentes.",
        COMMON_AUTH,
        SUPERVISOR,
        "Sin body",
        "list[SlaPolicyRead]",
        "401/403",
    ),
    "POST /api/v1/sla/policies": RouteMeta(
        "Crea política SLA por prioridad, categoría y origen.",
        COMMON_AUTH,
        SUPERVISOR,
        "SlaPolicyCreate",
        "SlaPolicyRead (201)",
        "401/403; 409 policy_key duplicada; 422 categoría, calendario, zona o tiempos",
    ),
    "PATCH /api/v1/sla/policies/{policy_id}": RouteMeta(
        "Actualiza una política SLA y conserva historial de valores anteriores/nuevos.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: policy_id; SlaPolicyUpdate parcial",
        "SlaPolicyRead",
        "401/403; 404; 409 policy_key duplicada; 422 campos nulos, zona o tiempos",
    ),
    "POST /api/v1/tickets/{ticket_id}/attachments": RouteMeta(
        "Carga un archivo multipart asociado a un ticket y opcionalmente a un comentario.",
        COMMON_AUTH,
        "CLIENTE/ASESOR/SUPERVISOR con acceso al ticket",
        "Multipart: file requerido, comment_id opcional",
        "AttachmentRead (201)",
        "401/403; 404 ticket/comentario; 413 tamaño/cuota; 415 MIME/extensión; 422 nombre/contenido",
    ),
    "GET /api/v1/tickets/{ticket_id}/attachments": RouteMeta(
        "Lista metadatos de adjuntos activos del ticket.",
        COMMON_AUTH,
        "CLIENTE/ASESOR/SUPERVISOR con acceso al ticket",
        "Path: ticket_id",
        "list[AttachmentRead]",
        "401; 403; 404",
    ),
    "GET /api/v1/attachments/{attachment_id}/download": RouteMeta(
        "Descarga el binario con Content-Disposition attachment, no-store y MIME detectado.",
        COMMON_AUTH,
        "CLIENTE/ASESOR/SUPERVISOR con acceso al ticket",
        "Path: attachment_id",
        "StreamingResponse binaria",
        "401; 403; 404 adjunto o archivo físico",
    ),
    "DELETE /api/v1/attachments/{attachment_id}": RouteMeta(
        "Marca un adjunto como DELETED, registra historial y elimina el objeto local.",
        COMMON_AUTH,
        "CLIENTE/ASESOR/SUPERVISOR con acceso al ticket",
        "Path: attachment_id",
        "AttachmentRead",
        "401; 403; 404",
    ),
    "GET /api/v1/notifications": RouteMeta(
        "Lista notificaciones propias paginadas, ordenadas por created_at e id descendentes.",
        COMMON_AUTH,
        ANY_ROLE,
        "Query: page=1, page_size=20 (1..100)",
        "NotificationPage",
        "401; 422 paginación",
    ),
    "GET /api/v1/notifications/unread-count": RouteMeta(
        "Cuenta notificaciones no leídas del usuario actual.",
        COMMON_AUTH,
        ANY_ROLE,
        "Sin body",
        "UnreadCountRead",
        "401",
    ),
    "POST /api/v1/notifications/read-all": RouteMeta(
        "Marca como leídas todas las notificaciones propias y devuelve el contador actualizado.",
        COMMON_AUTH,
        ANY_ROLE,
        "Sin body",
        "ReadAllResponse",
        "401",
    ),
    "PATCH /api/v1/notifications/{notification_id}/read": RouteMeta(
        "Marca como leída una notificación propia.",
        COMMON_AUTH,
        ANY_ROLE,
        "Path: notification_id",
        "NotificationRead",
        "401; 403 no pertenece al usuario; 404",
    ),
    "GET /api/v1/reports/summary": RouteMeta(
        "Resumen de tickets por estado y promedio de resolución.",
        COMMON_AUTH,
        SUPERVISOR,
        "Query: from?, to?, category_id?, status?, priority?",
        "SummaryReport",
        "401/403; 422 rango inválido",
    ),
    "GET /api/v1/reports/by-status": RouteMeta(
        "Agrupa tickets por estado.",
        COMMON_AUTH,
        SUPERVISOR,
        "Query: from?, to?, category_id?, status?, priority?",
        "StatusReport",
        "401/403; 422 rango inválido",
    ),
    "GET /api/v1/reports/by-category": RouteMeta(
        "Agrupa tickets por categoría.",
        COMMON_AUTH,
        SUPERVISOR,
        "Query: from?, to?, category_id?, status?, priority?",
        "CategoryReport",
        "401/403; 422 rango inválido",
    ),
    "GET /api/v1/reports/by-priority": RouteMeta(
        "Agrupa tickets por prioridad.",
        COMMON_AUTH,
        SUPERVISOR,
        "Query: from?, to?, category_id?, status?, priority?",
        "PriorityReport",
        "401/403; 422 rango inválido",
    ),
    "GET /api/v1/reports/resolution-time": RouteMeta(
        "Devuelve cantidad resuelta y promedio de tiempo de resolución.",
        COMMON_AUTH,
        SUPERVISOR,
        "Query: from?, to?, category_id?, status?, priority?",
        "ResolutionTimeReport",
        "401/403; 422 rango inválido",
    ),
    "GET /api/v1/reports/{report_name}/export": RouteMeta(
        "Genera exportación CSV de un reporte permitido y audita el intento.",
        COMMON_AUTH,
        SUPERVISOR,
        "Path: report_name; Query: format=csv|xlsx y filtros from, to, category_id, status, priority, source, advisor_id, client_id, sla_compliant, search, limit",
        "text/csv; XLSX no disponible",
        "401/403; 422 formato/rango/límite; 500 error de generación",
    ),
    "GET /api/v1/audit": RouteMeta(
        "Consulta auditoría global append-only con paginación y filtros.",
        COMMON_AUTH,
        SUPERVISOR,
        "Query: page=1, page_size=20, actor_user_id?, resource_type?, resource_id?, event_type?, action?, target_user_id?, from?, to?, success?, search?",
        "AuditPage",
        "401/403; 422 rango o paginación",
    ),
}


def default_meta(route: Route) -> RouteMeta:
    if route.path.startswith("/api/v1/auth/") and route.path not in {
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
        "/api/v1/auth/change-password",
    }:
        auth, role = "No", "-"
    elif (
        route.path in {"/api/v1/faqs", "/api/v1/faqs/{faq_id}", "/api/v1/categories"}
        and route.method == "GET"
    ):
        auth, role = "No", "-"
    elif route.path.startswith("/api/v1/chat/") and route.path not in {
        "/api/v1/chat/conversations/{conversation_id}/link-user",
        "/api/v1/chat/conversations/{conversation_id}/convert-to-ticket",
    }:
        auth, role = "Opcional", "Visitante o CLIENTE"
    else:
        auth, role = COMMON_AUTH, ANY_ROLE
    return RouteMeta(
        f"Operación {route.function} expuesta por el router del módulo.",
        auth,
        role,
        "Consultar esquema de entrada en el código fuente.",
        route.response,
        "422 validación; errores de autorización según el rol",
    )


def source_link(route: Route) -> str:
    return f"`{route.file}:{route.line}`"


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    def clean(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", "<br>")

    body = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    body.extend("| " + " | ".join(clean(value) for value in row) + " |" for row in rows)
    return "\n".join(body)


def actual_counts(routes: list[Route], models: list[dict[str, Any]]) -> dict[str, int]:
    service_files = count_files("**/*_service.py", ROOT / "app/modules")
    repository_files = count_files("**/*_repository.py", ROOT / "app/modules")
    test_files = count_files("**/test_*.py", ROOT / "tests")
    test_functions = 0
    for path in (ROOT / "tests").glob("**/test_*.py"):
        text = path.read_text(encoding="utf-8")
        test_functions += len(re.findall(r"^\s*def test_", text, re.MULTILINE))
    test_cases = test_functions
    pytest_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if pytest_python.exists():
        collected = subprocess.run(
            [str(pytest_python), "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        match = re.search(
            r"(\d+) tests? collected", f"{collected.stdout}\n{collected.stderr}"
        )
        if match:
            test_cases = int(match.group(1))
    return {
        "modules": sum(
            1
            for path in (ROOT / "app/modules").iterdir()
            if path.is_dir() and path.name != "__pycache__"
        ),
        "routes": len(routes),
        "entities": len(models),
        "repositories": repository_files,
        "services": service_files,
        "test_files": test_files,
        "test_functions": test_functions,
        "test_cases": test_cases,
    }


def module_rows() -> list[list[str]]:
    return [
        [
            "usuarios",
            "Identidad, JWT, sesiones, tokens de un solo uso, perfiles, preferencias y rate limiting.",
            "auth_router.py, user_router.py; AuthService, UserProfileService, UserService, AuthRateLimiter",
        ],
        [
            "tickets",
            "Creación, consulta, asignación, comentarios, estados, operaciones e historial.",
            "TicketService, TicketRepository; modelos Ticket, Assignment, Comment, History",
        ],
        [
            "conocimiento",
            "Categorías y FAQs públicas/editoriales, feedback, versiones y métricas.",
            "FAQService, CategoryService; FAQRepository, CategoryRepository",
        ],
        [
            "chatbot",
            "Conversaciones anónimas o autenticadas, matching FAQ, guardrails, feedback, escalamiento y conversión.",
            "ChatbotService, RAGService; ConversationRepository",
        ],
        [
            "adjuntos",
            "Carga, validación, almacenamiento local, descarga y eliminación lógica de archivos.",
            "AttachmentService, LocalStorageProvider; AttachmentRepository",
        ],
        [
            "notificaciones",
            "Persistencia in-app, preferencias, deduplicación y puerto de entrega desacoplado.",
            "NotificationService, NoopNotificationProvider",
        ],
        [
            "reportes",
            "Reportes operativos y exportaciones CSV auditadas.",
            "ReportService, ReportRepository; ReportExportAudit",
        ],
        [
            "auditoria",
            "Auditoría global append-only, redacción y consulta supervisada.",
            "AuditService, AuditRepository; AuditLog",
        ],
    ]


def entity_rows(models: list[dict[str, Any]]) -> list[list[str]]:
    notable = {
        "attachments": "id, ticket_id, comment_id?, uploaded_by_user_id, filename, MIME declarado/detectado, size, sha256, status, timestamps",
        "audit_logs": "id, event_type, action, actor/target, resource, result, redacted before/after/metadata, correlation/request ids",
        "auth_rate_limits": "id, action, hashed key, window, attempts, blocked_until",
        "auth_sessions": "id, user_id, issued_at, expires_at, revoked_at",
        "auth_tokens": "id, user_id, purpose, token_hash, expiry, used_at",
        "chat_messages": "id, conversation_id, sender_type, content, intent, confidence, faq_id, response_source, ai_trace_json",
        "conversations": "id, user_id?, anonymous_key, status, context, FAQ/category, turn_count, confidence, escalation/conversion timestamps",
        "faq_feedback": "id, faq_id, user_id?, is_helpful, escalation_accepted?, comment, created_at",
        "faq_versions": "id, faq_id, version, editorial content, status, active/publication dates, changed_by, action",
        "faqs": "id, category, content, normalized search fields, status, priority/order/version, active flag, authors and publication dates",
        "notifications": "id, recipient, type, message, related ticket/conversation, read state, metadata, dedupe_key",
        "report_export_audits": "id, actor, report, format, filters, row_count, succeeded, error_code, created_at",
        "roles": "id, unique name, description",
        "sla_policies": "id, unique policy_key, priority/category/source scope, response/resolution/warning seconds, UTC/24x7, active",
        "sla_policy_history": "id, policy_id, actor_id, action, old/new JSON values, created_at",
        "ticket_assignments": "id, ticket, advisor, assigned_by, assigned_at, unassigned_at",
        "ticket_categories": "id, unique name, description, active flag, timestamps",
        "ticket_comments": "id, ticket, author, content, created_at",
        "ticket_history": "id, ticket, actor, action, old/new values, description, created_at",
        "ticket_slas": "id, unique ticket_id, policy, due dates, responses, pause/breach/completion fields, reopen_count",
        "tickets": "id, unique tracking_code, client, optional conversation, category, subject/description, priority/status/source, assignment, version/timestamps",
        "user_preferences": "PK/FK user_id, channel flags, assignment/status/comment/SLA flags, language, timezone, timestamps",
        "user_profile_audits": "id, actor/affected user, field, redacted old/new values, created_at",
        "users": "id, role_id, identity, password_hash, phone, active/email_verified flags, session invalidation timestamp",
    }
    rows = []
    for model in models:
        fks = ", ".join(model["foreign_keys"]) or "-"
        rows.append(
            [
                model["name"],
                model["table"],
                notable.get(model["table"], ", ".join(model["fields"])),
                fks,
            ]
        )
    return rows


def service_rows() -> list[list[str]]:
    return [
        [
            "usuarios",
            "AuthService, UserProfileService, UserService, AuthRateLimiter",
            "auth, perfiles/preferencias, directorio de asesores y límites de autenticación",
        ],
        [
            "tickets",
            "TicketService, SlaService",
            "tickets, estados, asignaciones, SLA, historial y notificaciones",
        ],
        [
            "conocimiento",
            "FAQService, CategoryService",
            "editorial de FAQs/categorías, matching y feedback",
        ],
        [
            "chatbot",
            "ChatbotService, RAGService",
            "conversación, recuperación local, guardrails, proveedor IA opcional",
        ],
        [
            "adjuntos",
            "AttachmentService, LocalStorageProvider",
            "validación de archivos y almacenamiento privado local",
        ],
        [
            "notificaciones",
            "NotificationService, NoopNotificationProvider",
            "preferencias, persistencia y entrega desacoplada",
        ],
        [
            "reportes",
            "ReportService",
            "métricas, exportación CSV y auditoría de exportaciones",
        ],
        [
            "auditoria",
            "AuditService",
            "redacción, deduplicación y persistencia append-only",
        ],
    ]


def repository_rows() -> list[list[str]]:
    return [
        ["UserRepository", "users, roles", "email/id, role, asesores activos"],
        [
            "AuthRepository",
            "auth_sessions, auth_tokens, auth_rate_limits",
            "sesiones, tokens hash, invalidación y rate limit",
        ],
        [
            "UserProfileRepository",
            "user_preferences, user_profile_audits",
            "preferencias y auditorías de perfil",
        ],
        [
            "TicketRepository",
            "tickets, comments, assignments, history",
            "listados legacy/paginados/operativos, locks, filtros y consultas auxiliares",
        ],
        [
            "SlaRepository",
            "sla_policies, ticket_slas, sla_policy_history",
            "selección de policy, SLA por ticket y persistencia de historial",
        ],
        [
            "CategoryRepository",
            "ticket_categories",
            "categorías activas y por nombre/id",
        ],
        [
            "FAQRepository",
            "faqs, faq_versions, faq_feedback",
            "públicas/editoriales, relevancia, versiones y métricas",
        ],
        [
            "ConversationRepository",
            "conversations, chat_messages",
            "conversaciones, mensajes, cuotas y FAQs activas",
        ],
        [
            "AttachmentRepository",
            "attachments, ticket_comments, tickets",
            "metadatos, cuotas, adjuntos activos y soft delete",
        ],
        [
            "NotificationRepository",
            "notifications",
            "deduplicación, listados y lectura",
        ],
        [
            "ReportRepository",
            "tickets y tablas relacionadas",
            "agregados, export rows y límites",
        ],
        ["AuditRepository", "audit_logs", "append-only, filtros y paginación"],
    ]


def config_rows() -> list[list[str]]:
    return [
        [
            "APP_NAME, APP_ENV, DEBUG",
            "No",
            "Identidad de la API, entorno y debug.",
            "APP_ENV=development",
        ],
        [
            "DATABASE_URL",
            "Sí para ejecución real",
            "URL SQLAlchemy de MySQL con PyMySQL.",
            "mysql+pymysql://usuario:********@host:3306/tickets",
        ],
        [
            "SECRET_KEY, JWT_ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES",
            "SECRET_KEY sí fuera de desarrollo",
            "Firma HS256 y duración de access token.",
            "SECRET_KEY=********; JWT_ALGORITHM=HS256",
        ],
        [
            "PASSWORD_*",
            "No",
            "Política de longitud, mayúscula, minúscula, dígito y expiración de tokens.",
            "PASSWORD_MIN_LENGTH=8",
        ],
        [
            "AUTH_RATE_LIMIT_*",
            "No",
            "Intentos, ventana y bloqueo para autenticación.",
            "AUTH_RATE_LIMIT_MAX_ATTEMPTS=5",
        ],
        [
            "ATTACHMENT_*",
            "No",
            "Ruta, tamaño por archivo/ticket, cantidad, extensiones y MIME.",
            "ATTACHMENT_STORAGE_DIR=var/uploads",
        ],
        [
            "SLA_DEFAULT_*, SLA_POLICY_DEFAULTS",
            "No",
            "UTC, calendario 24x7 y tiempos por prioridad.",
            "SLA_DEFAULT_TIMEZONE=UTC",
        ],
        [
            "REPORT_EXPORT_MAX_*",
            "No",
            "Máximo de filas y rango de exportación.",
            "REPORT_EXPORT_MAX_ROWS=10000",
        ],
        [
            "CHATBOT_*",
            "No",
            "Umbrales, cuotas, longitud, contexto y comportamiento IA.",
            "CHATBOT_AI_ENABLED=false",
        ],
        [
            "CHATBOT_AI_PROVIDER, CHATBOT_AI_ENDPOINT, CHATBOT_AI_API_KEY, CHATBOT_AI_MODEL",
            "Solo para adaptadores IA opcionales",
            "Configuración del adaptador http_json; nunca documentar valores.",
            "CHATBOT_AI_API_KEY=********",
        ],
        [
            "OPENAI_API_KEY",
            "Solo para proveedor OpenAI opcional",
            "Clave del adaptador OpenAI Responses; nunca documentar el valor.",
            "OPENAI_API_KEY=********",
        ],
        [
            "TRUSTED_HOSTS, CORS_ORIGINS",
            "No",
            "Hosts aceptados y orígenes CORS.",
            'CORS_ORIGINS=["http://localhost:5173"]',
        ],
        [
            "MYSQL_*",
            "Docker Compose",
            "Credenciales y parámetros del contenedor MySQL.",
            "MYSQL_PASSWORD=********",
        ],
        [
            "DEMO_*_PASSWORD",
            "Seed local",
            "Contraseñas de las cuentas de demostración; solo deben usarse localmente.",
            "DEMO_CLIENT_PASSWORD=********",
        ],
    ]


def endpoint_description(route: Route, meta: RouteMeta) -> str:
    return (
        f"### {route.method} {route.path}\n\n"
        f"**Descripción.** {meta.description}\n\n"
        f"- Autenticación: {meta.auth}\n"
        f"- Rol o alcance: {meta.role}\n"
        f"- Entrada: {meta.request}\n"
        f"- Respuesta: HTTP {route.status} con `{meta.response}`\n"
        f"- Errores observables: {meta.errors}\n"
        f"- Implementación: `{route.function}` en {source_link(route)}\n"
    )


def mermaid_diagrams() -> dict[str, str]:
    return {
        "architecture": """flowchart LR\n  C[Cliente o frontend] --> API[FastAPI /api/v1]\n  API --> SEC[HTTPBearer + JWT + roles]\n  SEC --> MOD[Módulos de dominio]\n  MOD --> SVC[Services]\n  SVC --> REP[Repositories / SQLModel]\n  REP --> DB[(MySQL)]\n  SVC --> FS[(var/uploads local)]\n  SVC -. opcional .-> AI[Proveedor IA: OpenAI o http_json]\n""",
        "auth": """sequenceDiagram\n  actor U as Usuario\n  participant A as /auth/login\n  participant S as AuthService\n  participant DB as MySQL\n  participant F as get_current_user\n  U->>A: email + password\n  A->>S: authenticate + rate limit\n  S->>DB: usuario, role, auth_rate_limits\n  S->>DB: crear auth_sessions\n  S-->>U: JWT access token\n  U->>F: Authorization Bearer\n  F->>DB: valida usuario y jti/session\n  F-->>U: identidad y rol o 401/403\n""",
        "ticket": """sequenceDiagram\n  actor C as Cliente\n  participant T as Ticket router\n  participant S as TicketService\n  participant R as TicketRepository\n  participant SLA as SlaService\n  participant N as NotificationService\n  C->>T: POST /tickets\n  T->>S: create_manual\n  S->>R: valida categoría y guarda ticket\n  S->>SLA: start_for_ticket\n  S->>R: historial CREATED\n  S->>N: ticket_created\n  S-->>C: TicketRead 201\n""",
        "er": """erDiagram\n  ROLES ||--o{ USERS : role_id\n  USERS ||--o{ TICKETS : client_id\n  USERS ||--o{ TICKETS : assigned_advisor_id\n  TICKET_CATEGORIES ||--o{ TICKETS : category_id\n  TICKETS ||--o{ TICKET_COMMENTS : ticket_id\n  TICKETS ||--o{ TICKET_ASSIGNMENTS : ticket_id\n  TICKETS ||--o{ TICKET_HISTORY : ticket_id\n  TICKETS ||--|| TICKET_SLAS : ticket_id\n  SLA_POLICIES ||--o{ TICKET_SLAS : policy_id\n  USERS ||--o{ CONVERSATIONS : user_id\n  CONVERSATIONS ||--o{ CHAT_MESSAGES : conversation_id\n  FAQS ||--o{ CHAT_MESSAGES : faq_id\n  TICKET_CATEGORIES ||--o{ FAQS : category_id\n  FAQS ||--o{ FAQ_VERSIONS : faq_id\n  TICKETS ||--o{ ATTACHMENTS : ticket_id\n""",
        "modules": """flowchart TB\n  U[usuarios] --> T[tickets]\n  T --> S[SLA]\n  T --> A[adjuntos]\n  T --> N[notificaciones]\n  K[conocimiento] --> C[chatbot]\n  C --> T\n  R[reportes] --> T\n  AU[auditoria] -. registra .-> U\n  AU -. registra .-> T\n  AU -. registra .-> C\n""",
    }


def build_markdown(
    routes: list[Route], models: list[dict[str, Any]], counts: dict[str, int]
) -> str:
    today = date.today().isoformat()
    route_rows = []
    for route in routes:
        meta = META.get(route_key(route), default_meta(route))
        route_rows.append(
            [route.method, route.path, meta.description, meta.auth, meta.role]
        )
    entity_table = entity_rows(models)
    lines = [
        "# Documentación oficial del backend",
        "",
        "> Ticket Management API - estado real del código fuente auditado",
        "",
        f"**Fecha de auditoría:** {today}  \n**Versión del proyecto:** 0.1.0  \n**Alcance:** backend de prototipo académico independiente; no se conecta a sistemas del Banco GNB Perú ni procesa operaciones bancarias reales.",
        "",
        "## Índice",
        "",
        "1. [Introducción y alcance](#1-introducción-y-alcance)",
        "2. [Descripción general](#2-descripción-general)",
        "3. [Tecnologías utilizadas](#3-tecnologías-utilizadas)",
        "4. [Arquitectura](#4-arquitectura)",
        "5. [Organización del código fuente](#5-organización-del-código-fuente)",
        "6. [Módulos y funcionalidades](#6-módulos-y-funcionalidades)",
        "7. [API REST](#7-api-rest)",
        "8. [Seguridad, autenticación y permisos](#8-seguridad-autenticación-y-permisos)",
        "9. [Persistencia y modelo de datos](#9-persistencia-y-modelo-de-datos)",
        "10. [Reglas de negocio y validaciones](#10-reglas-de-negocio-y-validaciones)",
        "11. [Integraciones externas](#11-integraciones-externas)",
        "12. [DTOs, errores y paginación](#12-dtos-errores-y-paginación)",
        "13. [Configuración y ejecución](#13-configuración-y-ejecución)",
        "14. [Pruebas e infraestructura](#14-pruebas-e-infraestructura)",
        "15. [Pendientes y observaciones](#15-pendientes-y-observaciones)",
        "16. [Matriz de trazabilidad y glosario](#16-matriz-de-trazabilidad-y-glosario)",
        "",
        "## 1. Introducción y alcance",
        "",
        "Este documento describe exclusivamente los componentes encontrados en `app/`, `migrations/`, `tests/`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example` y `README.md`. No se documentan endpoints, tablas, servicios externos ni reglas que no tengan respaldo en esos archivos.",
        "",
        "El backend es un monolito modular FastAPI con persistencia MySQL. Administra usuarios, autenticación, tickets, conocimiento FAQ, conversaciones de chatbot, SLA, adjuntos, notificaciones, reportes y auditoría. Se trata de un prototipo académico y el README declara expresamente que no es un sistema oficial bancario.",
        "",
        "## 2. Descripción general",
        "",
        "### Qué hace actualmente",
        "",
        "- Expone una API REST versionada bajo `/api/v1`.",
        "- Registra clientes y emite JWT de acceso; crea sesiones revocables y tokens hash para recuperación/verificación.",
        "- Permite a clientes crear y consultar tickets, conversar con un chatbot FAQ y convertir conversaciones no resueltas en tickets.",
        "- Permite a asesores operar tickets asignados y a supervisores administrar asignaciones, conocimiento, SLA, reportes y auditoría.",
        "- Persiste historiales operativos, notificaciones, versiones editoriales y auditoría global redactada.",
        "- Almacena adjuntos en el directorio local configurado, no en MySQL ni como archivos estáticos.",
        "",
        "### Estado actual",
        "",
        f"El inventario contiene {counts['modules']} módulos de dominio, {counts['routes']} rutas HTTP incluyendo health, {counts['entities']} entidades SQLModel, {counts['repositories']} repositorios y {counts['services']} archivos de servicio. La IA generativa y el envío real de correo/notificaciones están desacoplados mediante puertos, pero los adaptadores locales por defecto son `Noop`.",
        "",
        "## 3. Tecnologías utilizadas",
        "",
        md_table(
            ["Tecnología", "Versión declarada", "Función"],
            [
                ["Python", ">=3.11", "Lenguaje de aplicación"],
                [
                    "FastAPI",
                    ">=0.115,<1.0",
                    "API REST, validación y OpenAPI en desarrollo",
                ],
                [
                    "Pydantic / pydantic-settings",
                    "Pydantic >=2.6 indirecto; pydantic-settings >=2.6,<3.0",
                    "Schemas, configuración y validaciones",
                ],
                [
                    "SQLModel",
                    ">=0.0.22,<1.0",
                    "Modelos persistentes y sesiones SQLAlchemy",
                ],
                ["MySQL", "8.4 en Docker Compose", "Base de datos relacional"],
                ["PyMySQL", ">=1.1,<2.0", "Driver MySQL"],
                ["Alembic", ">=1.13,<2.0", "Migraciones versionadas"],
                ["PyJWT", ">=2.9,<3.0", "JWT HS256"],
                [
                    "pwdlib[argon2]",
                    ">=0.2,<1.0",
                    "Hash de contraseñas Argon2 recomendado",
                ],
                [
                    "pytest / pytest-cov / httpx",
                    "dev",
                    "Pruebas unitarias e integración con TestClient",
                ],
                ["ruff", ">=0.8,<1.0", "Lint y formato"],
                [
                    "Docker",
                    "python:3.12-slim; mysql:8.4",
                    "Ejecución reproducible local",
                ],
            ],
        ),
        "",
        "## 4. Arquitectura",
        "",
        "La implementación sigue una separación por módulo y responsabilidad: router -> service -> repository -> model. Los routers declaran contratos HTTP y dependencias FastAPI; los servicios aplican permisos y reglas de negocio; los repositorios encapsulan consultas SQLModel; los modelos son tablas. `app/shared` contiene utilidades transversales de paginación, fechas, texto y tipos.",
        "",
        "```mermaid",
        mermaid_diagrams()["architecture"].rstrip(),
        "```",
        "",
        "La configuración global incluye `TrustedHostMiddleware`, `CORSMiddleware` sin cookies, dos headers de seguridad HTTP y documentación pública únicamente cuando `APP_ENV=development`. La sesión SQLModel usa `pool_pre_ping=True`; Alembic toma la URL desde settings y apunta a `SQLModel.metadata`.",
        "",
        "## 5. Organización del código fuente",
        "",
        "```text",
        "app/",
        "├── api/deps.py                         # autenticación, usuario actual y roles",
        "├── api/v1/router.py                    # prefijo /api/v1 e inclusión de routers",
        "├── core/                               # configuración y logging",
        "├── db/                                 # engine, sesión y registro de modelos",
        "├── modules/                            # módulos por dominio",
        "│   ├── usuarios/{api,models,repositories,schemas,services}",
        "│   ├── tickets/{api,models,repositories,schemas,services}",
        "│   ├── conocimiento/{api,models,repositories,schemas,services}",
        "│   ├── chatbot/{api,models,repositories,schemas,services}",
        "│   ├── adjuntos/{api,models,repositories,schemas,services}",
        "│   ├── notificaciones/{api,models,repositories,schemas,services}",
        "│   ├── reportes/{api,models,repositories,schemas,services}",
        "│   └── auditoria/{api,models,repositories,schemas,services}",
        "├── seed/demo_data.py                    # datos locales idempotentes",
        "└── shared/                              # utilidades transversales",
        "migrations/versions/                    # 17 revisiones Alembic",
        "tests/{unit,integration,evaluation}/",
        "```",
        "",
        md_table(
            ["Módulo", "Responsabilidad", "Componentes principales"], module_rows()
        ),
        "",
        "## 6. Módulos y funcionalidades",
        "",
        "### Usuarios, autenticación y perfil",
        "",
        "Registro, login, logout, `GET /auth/me`, recuperación y cambio de contraseña, verificación de correo, perfiles propios, preferencias y directorio de asesores. El registro fuerza rol `CLIENTE`; la administración de roles no tiene endpoint HTTP. Los perfiles no exponen `password_hash`, sesiones ni secretos.",
        "",
        "### Tickets y operación interna",
        "",
        "Los tickets se crean manualmente o desde conversaciones; llevan `tracking_code` único, estado, prioridad, origen, asignación, versión optimista, timestamps y `ticket_history`. El asesor solo opera tickets asignados; el supervisor administra asignaciones, colas y cancelaciones.",
        "",
        "Estados: `NUEVO`, `ASIGNADO`, `EN_PROCESO`, `PENDIENTE_CLIENTE`, `RESUELTO`, `CERRADO`, `CANCELADO`. Transiciones codificadas: NUEVO -> ASIGNADO/CANCELADO; ASIGNADO -> EN_PROCESO/CANCELADO; EN_PROCESO -> PENDIENTE_CLIENTE/RESUELTO/CANCELADO; PENDIENTE_CLIENTE -> EN_PROCESO; RESUELTO -> CERRADO/EN_PROCESO/CANCELADO. CERRADO y CANCELADO son terminales.",
        "",
        "### Conocimiento y chatbot",
        "",
        "Las FAQs se normalizan sin acentos y con espacios colapsados. El matching determinista combina pregunta, título, resumen, respuesta, keywords, tags, sinónimos y categoría. Alta confianza responde con la FAQ; confianza media ofrece hasta tres opciones; baja confianza ofrece atención humana y mantiene el fallback. Solo FAQs publicadas, activas, vigentes y de categorías activas llegan al contexto RAG.",
        "",
        "Las conversaciones admiten estados `ACTIVE`, `WAITING_CLARIFICATION`, `ESCALATED`, `CONVERTED_TO_TICKET`, `CLOSED` y `EXPIRED`. Visitantes y clientes pueden usar el chatbot; asesores y supervisores son rechazados. El contenido no válido, terminal o fuera de cuota se rechaza.",
        "",
        "### SLA",
        "",
        "Cada ticket intenta iniciar un SLA según prioridad, categoría y origen. Se registran primera respuesta, vencimientos, pausas, advertencias, breaches, cumplimiento y reaperturas. Solo comentarios de asesor o supervisor cuentan como primera respuesta. La evaluación se ejecuta bajo demanda por un supervisor: no existe scheduler en el código.",
        "",
        "### Adjuntos, notificaciones, reportes y auditoría",
        "",
        "Los adjuntos se validan por nombre, extensión, MIME declarado, firma detectada, tamaño, cuota, conteo y hash SHA-256. Las notificaciones son in-app y usan deduplicación; el canal externo es un `NoopNotificationProvider`. Los reportes son solo para supervisores y exportan CSV seguro; XLSX devuelve 422. La auditoría global es append-only y no tiene mutaciones HTTP.",
        "",
        "## 7. API REST",
        "",
        f"Todas las rutas se registraron desde decoradores FastAPI del código: **{len(routes)} endpoints**. La documentación interactiva `/docs`, `/redoc` y `/openapi.json` solo se habilita en desarrollo.",
        "",
        "### Índice rápido por método",
        "",
        md_table(
            ["Método", "Cantidad", "Familias"],
            [
                [
                    method,
                    sum(route.method == method for route in routes),
                    ", ".join(
                        sorted(
                            {
                                route.path.split("/")[3]
                                for route in routes
                                if route.method == method
                            }
                        )
                    ),
                ]
                for method in ["GET", "POST", "PATCH", "DELETE"]
            ],
        ),
        "",
        "### Resumen completo",
        "",
        md_table(
            ["Método", "Endpoint", "Descripción", "Autenticación", "Rol"], route_rows
        ),
        "",
        "### Documentación individual",
        "",
    ]
    for route in routes:
        meta = META.get(route_key(route), default_meta(route))
        lines.extend([endpoint_description(route, meta), ""])
    lines.extend(
        [
            "## 8. Seguridad, autenticación y permisos",
            "",
            "El esquema HTTP usa `HTTPBearer(auto_error=False)`. `get_current_user` exige claims `exp`, `iat`, `sub` y `type`; acepta solo `type=access`, valida el usuario activo, el rol y, para tokens nuevos, la fila `auth_sessions` referenciada por `jti`. Los tokens legacy sin `jti` se aceptan hasta expiración, pero quedan invalidados si el usuario tiene `sessions_invalidated_at` posterior.",
            "",
            "Contraseñas: `pwdlib.PasswordHash.recommended()`; la política exige por defecto mínimo 8 caracteres, mayúscula, minúscula y dígito. Tokens de recuperación/verificación: se genera valor aleatorio, se guarda únicamente SHA-256, se marca `used_at` y se invalida el token anterior de la misma finalidad. El adaptador de correo local no envía ni registra tokens.",
            "",
            "La redacción global sustituye claves sensibles (password, hash, token, secret, API key, authorization, cookie, credential, email, etc.), limita profundidad a 6, colecciones a 100 elementos y strings a 512 caracteres. Guardrails del chatbot bloquean inyección, solicitudes de secretos, contenido sensible, salida con tokens/scripts, fuentes inválidas, salida fuera de contexto y exceso de longitud.",
            "",
            "CORS usa orígenes de `CORS_ORIGINS`, métodos GET/POST/PATCH/DELETE/OPTIONS y headers Authorization/Content-Type. `allow_credentials=False`. Trusted hosts usa `TRUSTED_HOSTS`. Headers añadidos: `X-Content-Type-Options: nosniff` y `Referrer-Policy: no-referrer`.",
            "",
            "```mermaid",
            mermaid_diagrams()["auth"].rstrip(),
            "```",
            "",
            "### Roles",
            "",
            md_table(
                ["Rol", "Permisos observados"],
                [
                    [
                        "CLIENTE",
                        "Crear/consultar/comentar propios tickets; usar chatbot; asociar y convertir conversaciones; feedback FAQ; perfil, preferencias y notificaciones propias.",
                    ],
                    [
                        "ASESOR",
                        "Consultar tickets asignados; operar estados y comentarios de tickets asignados; tomar/liberar según reglas; ver SLA y operar colas internas.",
                    ],
                    [
                        "SUPERVISOR",
                        "Todo lo interno global; asignar/reasignar/cancelar; gestionar FAQs/categorías; gestionar políticas SLA; evaluar SLA; reportes/exportaciones; directorio de asesores; auditoría global.",
                    ],
                ],
            ),
            "",
            "## 9. Persistencia y modelo de datos",
            "",
            "El motor declarado es MySQL, conectado mediante `mysql+pymysql`. SQLModel registra 24 tablas en `app/db/models.py`. Alembic configura `SQLModel.metadata` y contiene 17 migraciones, desde `0001_initial_schema` hasta `0017_chatbot_ai_trace`. No se detectaron queries SQL nativas, procedures, locks optimistas de base de datos ni motor vectorial; sí existe `SELECT ... FOR UPDATE` en operaciones mutables de tickets y control optimista por `Ticket.version`/`expected_version`.",
            "",
            md_table(
                ["Modelo", "Tabla", "Campos principales", "Claves foráneas"],
                entity_table,
            ),
            "",
            "### Relaciones principales",
            "",
            "```mermaid",
            mermaid_diagrams()["er"].rstrip(),
            "```",
            "",
            "Las relaciones anteriores se derivan de `Field(foreign_key=...)` y `ForeignKey(...)` del modelo. No se agregan relaciones ORM implícitas que no estén declaradas.",
            "",
            "### Servicios y repositorios",
            "",
            md_table(["Módulo", "Servicios", "Responsabilidad"], service_rows()),
            "",
            md_table(
                ["Repositorio", "Tablas", "Operaciones relevantes"], repository_rows()
            ),
            "",
            "## 10. Reglas de negocio y validaciones",
            "",
            md_table(
                ["Código", "Regla detectada", "Módulo / evidencia"],
                [
                    [
                        "RN-01",
                        "El registro crea únicamente CLIENTE.",
                        "usuarios / AuthService.register",
                    ],
                    [
                        "RN-02",
                        "Un JWT nuevo se vincula a auth_sessions y logout revoca esa sesión.",
                        "usuarios / AuthService, deps.py",
                    ],
                    [
                        "RN-03",
                        "Password reset y email verification usan tokens hash, expirables y de un solo uso.",
                        "usuarios / AuthToken, AuthService",
                    ],
                    [
                        "RN-04",
                        "La recuperación devuelve mensaje uniforme para evitar enumeración.",
                        "usuarios / auth_router.py",
                    ],
                    [
                        "RN-05",
                        "Solo CLIENTE crea tickets manuales o convierte conversaciones.",
                        "tickets / TicketService",
                    ],
                    [
                        "RN-06",
                        "Una categoría usada por tickets no puede desactivarse.",
                        "conocimiento / CategoryService",
                    ],
                    [
                        "RN-07",
                        "Una FAQ se publica solo desde transición válida, con categoría activa y contenido válido.",
                        "conocimiento / FAQService",
                    ],
                    [
                        "RN-08",
                        "Editar contenido publicado devuelve la FAQ a REVIEW y crea snapshot/version.",
                        "conocimiento / FAQService",
                    ],
                    [
                        "RN-09",
                        "El chatbot acepta visitantes o CLIENTE; personal interno no puede utilizarlo.",
                        "chatbot / ChatbotService",
                    ],
                    [
                        "RN-10",
                        "Conversaciones terminales no aceptan nuevos mensajes y una conversión duplicada devuelve 409.",
                        "chatbot / ChatbotService, TicketService",
                    ],
                    [
                        "RN-11",
                        "Los tickets cerrados o cancelados no se modifican.",
                        "tickets / TicketService",
                    ],
                    [
                        "RN-12",
                        "Cancelar exige motivo y rol SUPERVISOR; reabrir RESUELTO exige motivo.",
                        "tickets / TicketService",
                    ],
                    [
                        "RN-13",
                        "Las mutaciones de tickets incrementan version y pueden validar expected_version.",
                        "tickets / TicketService",
                    ],
                    [
                        "RN-14",
                        "Solo asesor/supervisor cuentan como primera respuesta SLA.",
                        "tickets / SlaService",
                    ],
                    [
                        "RN-15",
                        "La evaluación SLA es idempotente por timestamps/dedupe de notificaciones y se invoca bajo demanda.",
                        "tickets / SlaService",
                    ],
                    [
                        "RN-16",
                        "Adjuntos se aceptan solo con extensión, MIME y firma compatibles, cuotas y acceso autorizado.",
                        "adjuntos / AttachmentService",
                    ],
                    [
                        "RN-17",
                        "Notificaciones críticas de seguridad no se pueden desactivar.",
                        "usuarios/notificaciones / PreferencesUpdate, NotificationService",
                    ],
                    [
                        "RN-18",
                        "Solo SUPERVISOR consulta reportes, exporta y consulta auditoría global.",
                        "reportes/auditoria / services y deps",
                    ],
                    [
                        "RN-19",
                        "La exportación solo entrega CSV; XLSX responde 422.",
                        "reportes / ReportService",
                    ],
                ],
            ),
            "",
            "### Validaciones de entrada",
            "",
            "Pydantic valida longitudes, enums, correos, rangos, listas de tags/sinónimos (máximo 20 términos de 80 caracteres), `page/page_size` (1..100), teléfonos, zonas horarias IANA, tiempos SLA (1..31.536.000 segundos) y cuerpos con `extra=forbid` donde corresponde. FastAPI genera 422 para fallos de contrato.",
            "",
            "### Validaciones de negocio y persistencia",
            "",
            "Los servicios validan existencia/actividad de categorías y asesores, propiedad/alcance por rol, estados terminales, transiciones, versión esperada, fechas de rango, cuotas de chatbot/adjuntos/exportación, duplicidad de FAQ/policy keys, publicación, rate limiting, tokens y acceso a notificaciones. Los modelos agregan PK, FK, unique constraints e índices.",
            "",
            "## 11. Integraciones externas",
            "",
            "Durante el análisis no se identificaron integraciones externas activas por defecto. Existe un puerto opcional de IA con dos adaptadores: `OpenAIResponsesProvider`, que requiere habilitación explícita, `CHATBOT_AI_PROVIDER=openai` y `OPENAI_API_KEY`; y `HttpJsonAIProvider`, que requiere `CHATBOT_AI_PROVIDER=http_json`, endpoint, API key y modelo.",
            "",
            md_table(
                ["Integración", "Estado", "Contrato real", "Seguridad y errores"],
                [
                    [
                        "OpenAI Responses API",
                        "Opcional; por defecto Noop/deshabilitado",
                        "POST con `store=false`, salida JSON estructurada (`text`, `source_ids`) y contexto FAQ autorizado; el modelo se configura por variable.",
                        "Requiere `CHATBOT_AI_ENABLED=true` y `OPENAI_API_KEY`; timeout, errores HTTP/red o salida inválida conservan el fallback seguro.",
                    ],
                    [
                        "Gateway IA HTTP JSON",
                        "Opcional; por defecto Noop/deshabilitado",
                        "POST al endpoint configurado con model, input, policy, prompt_version y context autorizado; espera text y source_ids.",
                        "Bearer API key desde variable; timeout configurable (15 s por defecto); timeout/error/respuesta inválida -> fallback; circuito tras fallos consecutivos.",
                    ],
                    [
                        "Correo",
                        "Noop local",
                        "Puerto EmailProvider para reset/verificación; el adaptador incluido no envía, guarda ni loguea tokens.",
                        "No hay SMTP ni SDK real en el repositorio.",
                    ],
                    [
                        "Entrega de notificaciones",
                        "Noop local",
                        "Puerto NotificationProvider; persistencia in-app permanece local.",
                        "Proveedor externo no configurado; errores de entrega son best effort.",
                    ],
                    [
                        "Almacenamiento de archivos",
                        "Local",
                        "LocalStorageProvider en ATTACHMENT_STORAGE_DIR.",
                        "Path traversal bloqueado; raíz privada 0700; clave interna aleatoria; no hay cloud storage.",
                    ],
                ],
            ),
            "",
            "Ambos adaptadores IA reciben entrada minimizada, contexto FAQ autorizado y una salida acotada; las fuentes devueltas se validan antes de aceptar una respuesta. La clave OpenAI y la API key del gateway nunca se exponen al frontend. El adaptador OpenAI envía `store=false`; esto no equivale a una garantía de Zero Data Retention del proveedor.",
            "",
            "## 12. DTOs, errores y paginación",
            "",
            "Los contratos públicos están en `schemas/`: auth/profile, tickets/operations/SLA, FAQ/category, chatbot, attachments, notifications, reports y audit. Los `Read` usan `from_attributes` cuando se construyen desde SQLModel. Los modelos de respuesta de tickets, FAQs y notificaciones usan una envoltura paginada con `page`, `page_size`, `total`, `total_pages` e `items`.",
            "",
            "Los endpoints de tickets y FAQs mantienen compatibilidad: sin `page` ni `page_size` devuelven listas legacy; al enviar cualquiera de ellos devuelven página. Páginas fuera de rango devuelven `items: []`. Los listados ordenan tickets por `created_at DESC, id DESC`; notificaciones por `created_at DESC, id DESC`; comentarios e historiales siguen el orden registrado por repositorio.",
            "",
            'No existe un `@ControllerAdvice`/manejador global equivalente: los routers y servicios convierten errores de dominio conocidos en `HTTPException`; errores no previstos quedan a cargo de FastAPI/Starlette. Los formatos observables más comunes son el detalle FastAPI (`{"detail": ...}`), respuestas de validación 422 y respuestas binarias para descarga/exportación.',
            "",
            "## 13. Configuración y ejecución",
            "",
            md_table(
                ["Variable o grupo", "Obligatoria", "Descripción", "Ejemplo seguro"],
                config_rows(),
            ),
            "",
            "### Ejecución local documentada por el proyecto",
            "",
            "```bash",
            "python -m venv .venv",
            'pip install -e ".[dev]"',
            "docker compose up -d db",
            "alembic upgrade head",
            "python -m app.seed.demo_data",
            "fastapi dev app/main.py",
            "pytest",
            "ruff check .",
            "ruff format --check .",
            "```",
            "",
            "Swagger queda en `http://localhost:8000/docs` solo en development; health es `GET /api/v1/health`. Docker Compose publica MySQL en `3307` y API en `8000`, usa volumen `mysql_data` y espera el healthcheck de MySQL antes de iniciar API.",
            "",
            "## 14. Pruebas e infraestructura",
            "",
            f"Se encontraron {counts['test_files']} archivos de prueba, {counts['test_functions']} funciones `test_*` y {counts['test_cases']} casos recogidos por Pytest (incluye parametrizaciones). Hay pruebas unitarias (`tests/unit`), integración (`tests/integration`) y evaluación de calidad del chatbot (`tests/evaluation`). Cubren auth/security, health, CORS, tickets, paginación, comentarios, operaciones de asesores, SLA, adjuntos, notificaciones, perfil/preferencias, knowledge base, chatbot fases 11/12, reports/exports, audit y seed.",
            "",
            md_table(
                ["Tipo", "Ubicación", "Alcance"],
                [
                    [
                        "Unitarias",
                        "tests/unit/test_database_models.py, test_seed.py",
                        "Modelos SQLModel, seed e idempotencia.",
                    ],
                    [
                        "Integración",
                        "tests/integration/*.py",
                        "API con TestClient, base de datos de prueba y reglas de permisos.",
                    ],
                    [
                        "Evaluación",
                        "tests/evaluation/test_chatbot_phase12_quality.py",
                        "Calidad y guardrails del flujo IA/RAG.",
                    ],
                ],
            ),
            "",
            "Validación ejecutada durante esta auditoría: `pytest` -> 219 passed, 2 warnings; `ruff check .` -> All checks passed; `ruff format --check .` -> 205 files already formatted. `alembic check` no pudo conectarse porque no había MySQL escuchando en `localhost:3307`; no se modificó el backend ni la base de datos.",
            "",
            "```mermaid",
            mermaid_diagrams()["modules"].rstrip(),
            "```",
            "",
            "## 15. Pendientes y observaciones",
            "",
            "### Funcionalidades pendientes o parcialmente implementadas",
            "",
            "- El proveedor de correo incluido es `NoopEmailProvider`; el envío real debe inyectarse fuera de este prototipo.",
            "- El proveedor de notificaciones incluido es `NoopNotificationProvider`; no existe entrega externa activa.",
            "- La IA generativa es opcional y está deshabilitada por defecto; OpenAI requiere habilitación y clave explícitas, mientras que `http_json` requiere un gateway aprobado y sus credenciales.",
            "- `format=xlsx` está representado en el enum de reportes, pero el servicio responde 422 porque no existe una dependencia XLSX.",
            "- La evaluación SLA es manual mediante endpoint protegido; no hay scheduler, Celery, Redis ni proceso de fondo.",
            "- No hay acciones masivas de tickets.",
            "",
            "### Observaciones técnicas descriptivas",
            "",
            "- `GET /api/v1/health` devuelve estado estático y no verifica conectividad de MySQL.",
            "- `alembic.ini` contiene una URL local de desarrollo y `migrations/env.py` la sustituye por `DATABASE_URL` de settings.",
            "- La documentación OpenAPI se desactiva fuera de development mediante `docs_url`, `redoc_url` y `openapi_url`.",
            "- La auditoría global complementa, pero no reemplaza, historiales específicos de tickets, SLA, perfil, FAQs y exportaciones.",
            "- Las tablas y consultas usan índices específicos para listados, estados, fechas, asignaciones, SLA y auditoría.",
            "- El `pass` encontrado en adaptadores locales/capturas de errores representa fallbacks intencionales; no se identificó un método de negocio expuesto con `NotImplemented` o `UnsupportedOperationException`.",
            "",
            "## 16. Matriz de trazabilidad y glosario",
            "",
            "### Matriz de trazabilidad de endpoints",
            "",
            md_table(
                [
                    "Funcionalidad",
                    "Endpoint",
                    "Controller",
                    "Service",
                    "Repository",
                    "Entidades",
                ],
                [
                    [
                        META.get(route_key(route), default_meta(route)).description[
                            :80
                        ],
                        route.path,
                        route.file,
                        service_for(route),
                        repository_for(route),
                        entities_for(route),
                    ]
                    for route in routes
                ],
            ),
            "",
            "### Dependencias principales",
            "",
            md_table(
                ["Dependencia", "Versión", "Finalidad"],
                [
                    [
                        "fastapi[standard]",
                        ">=0.115,<1.0",
                        "API y servidor de desarrollo/ejecución",
                    ],
                    ["sqlmodel", ">=0.0.22,<1.0", "ORM/modelos"],
                    ["alembic", ">=1.13,<2.0", "Migraciones"],
                    ["PyMySQL", ">=1.1,<2.0", "Conector MySQL"],
                    ["PyJWT", ">=2.9,<3.0", "Tokens JWT"],
                    ["pwdlib[argon2]", ">=0.2,<1.0", "Hash de contraseñas"],
                    ["httpx", ">=0.27,<1.0 dev", "Cliente de pruebas/TestClient"],
                    ["pytest", ">=8.0,<9.0 dev", "Pruebas"],
                    ["ruff", ">=0.8,<1.0 dev", "Lint y formato"],
                ],
            ),
            "",
            "### Glosario",
            "",
            "- **CLIENTE:** rol que crea tickets propios y usa el chatbot.",
            "- **ASESOR:** rol interno que atiende tickets asignados.",
            "- **SUPERVISOR:** rol interno con administración global.",
            "- **FAQ:** contenido de conocimiento publicado para el chatbot y consulta pública.",
            "- **SLA:** registro de tiempos de primera respuesta y resolución asociado a un ticket.",
            "- **RAG local:** recuperación determinista de FAQs autorizadas usada como contexto opcional para IA.",
            "- **Noop provider:** adaptador local que no ejecuta entrega externa.",
            "- **Dedupe key:** clave única para evitar duplicar auditorías/notificaciones.",
            "- **expected_version:** control optimista para evitar sobrescribir mutaciones concurrentes.",
            "",
            "### Conclusión técnica",
            "",
            "El backend actual es un prototipo modular funcional de gestión de tickets con autenticación, conocimiento FAQ, chatbot determinista, operación interna, SLA, adjuntos locales, notificaciones in-app, reportes CSV y trazabilidad. La evidencia del repositorio muestra que la base está preparada para adaptadores externos, pero estos no están activos por defecto. Esta conclusión refleja el estado del código auditado y no una hoja de ruta futura.",
            "",
            "### Resumen de auditoría",
            "",
            md_table(
                ["Métrica", "Cantidad / resultado"],
                [
                    ["Módulos", counts["modules"]],
                    ["Endpoints HTTP", counts["routes"]],
                    ["Entidades persistentes", counts["entities"]],
                    ["Repositories", counts["repositories"]],
                    ["Archivos de servicios", counts["services"]],
                    ["Roles", 3],
                    ["Integraciones externas activas", 0],
                    ["Puertos opcionales de integración IA", 1],
                    ["Adaptadores IA disponibles", 2],
                    ["Archivos de prueba", counts["test_files"]],
                    ["Funciones de prueba definidas", counts["test_functions"]],
                    ["Casos recogidos por Pytest", counts["test_cases"]],
                    [
                        "Archivos generados",
                        "docs/DOCUMENTACION_OFICIAL_BACKEND.md; output/pdf/DOCUMENTACION_OFICIAL_BACKEND.pdf",
                    ],
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def service_for(route: Route) -> str:
    path = route.path
    if "/auth" in path:
        return "AuthService"
    if "/users" in path:
        return "UserService / UserProfileService"
    if "/faqs" in path:
        return "FAQService"
    if "/categories" in path:
        return "CategoryService"
    if "/chat" in path:
        return "ChatbotService / TicketService"
    if "/sla" in path or path.startswith("/api/v1/sla"):
        return "SlaService"
    if "/attachments" in path:
        return "AttachmentService"
    if "/notifications" in path:
        return "NotificationService"
    if "/reports" in path:
        return "ReportService"
    if "/audit" in path:
        return "AuditRepository (consulta)"
    if "/tickets" in path:
        return "TicketService"
    return "-"


def repository_for(route: Route) -> str:
    path = route.path
    if "/auth" in path or "/users" in path:
        return "UserRepository / AuthRepository / UserProfileRepository"
    if "/faqs" in path:
        return "FAQRepository / CategoryRepository"
    if "/categories" in path:
        return "CategoryRepository"
    if "/chat" in path:
        return "ConversationRepository / FAQRepository / TicketRepository"
    if "/sla" in path or path.startswith("/api/v1/sla"):
        return "SlaRepository"
    if "/attachments" in path:
        return "AttachmentRepository"
    if "/notifications" in path:
        return "NotificationRepository"
    if "/reports" in path:
        return "ReportRepository"
    if "/audit" in path:
        return "AuditRepository"
    if "/tickets" in path:
        return "TicketRepository"
    return "-"


def entities_for(route: Route) -> str:
    path = route.path
    if "/auth" in path or "/users" in path:
        return "User, Role, AuthSession, AuthToken, AuthRateLimit, UserPreference, UserProfileAudit"
    if "/faqs" in path:
        return "FAQ, FAQVersion, FAQFeedback, TicketCategory"
    if "/categories" in path:
        return "TicketCategory"
    if "/chat" in path:
        return "Conversation, ChatMessage, FAQ, FAQFeedback, Ticket"
    if "/sla" in path or path.startswith("/api/v1/sla"):
        return "TicketSla, SlaPolicy, SlaPolicyHistory"
    if "/attachments" in path:
        return "Attachment, Ticket, TicketComment, TicketHistory"
    if "/notifications" in path:
        return "Notification, UserPreference"
    if "/reports" in path:
        return "Ticket, ReportExportAudit"
    if "/audit" in path:
        return "AuditLog"
    if "/tickets" in path:
        return "Ticket, TicketAssignment, TicketComment, TicketHistory, TicketSla"
    return "-"


class DiagramFlowable(Flowable):
    def __init__(self, kind: str, width: float = 500, height: float = 170) -> None:
        super().__init__()
        self.kind = kind
        self.width = width
        self.height = height

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        return min(self.width, avail_width), self.height

    def _box(
        self,
        canvas: Any,
        x: float,
        y: float,
        w: float,
        h: float,
        label: str,
        fill: colors.Color,
    ) -> None:
        canvas.setFillColor(fill)
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.roundRect(x, y, w, h, 6, stroke=1, fill=1)
        canvas.setFillColor(colors.HexColor("#0F172A"))
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawCentredString(x + w / 2, y + h / 2 - 3, label)

    def _arrow(self, canvas: Any, x1: float, y1: float, x2: float, y2: float) -> None:
        canvas.setStrokeColor(colors.HexColor("#2563EB"))
        canvas.setLineWidth(1.1)
        canvas.line(x1, y1, x2, y2)
        canvas.line(x2, y2, x2 - 5, y2 + 3)
        canvas.line(x2, y2, x2 - 5, y2 - 3)

    def draw(self) -> None:
        c = self.canv
        c.saveState()
        if self.kind == "architecture":
            labels = [
                ("Cliente", 10, 100),
                ("FastAPI", 105, 100),
                ("Seguridad", 200, 100),
                ("Services", 295, 100),
                ("Repositories", 390, 100),
                ("MySQL", 485, 100),
            ]
            for label, x, y in labels:
                self._box(c, x, y, 75, 32, label, colors.HexColor("#EFF6FF"))
            for x in [85, 180, 275, 370, 465]:
                self._arrow(c, x, 116, x + 20, 116)
            self._box(c, 200, 35, 110, 30, "var/uploads", colors.HexColor("#F0FDF4"))
            self._box(
                c, 355, 35, 130, 30, "IA OpenAI / http_json", colors.HexColor("#FFF7ED")
            )
            self._arrow(c, 333, 100, 255, 65)
            self._arrow(c, 390, 100, 420, 65)
        elif self.kind == "auth":
            labels = [
                ("Login", 20, 95),
                ("AuthService", 130, 95),
                ("auth_sessions", 240, 95),
                ("JWT", 350, 95),
                ("get_current_user", 430, 95),
            ]
            for label, x, y in labels:
                self._box(c, x, y, 95, 32, label, colors.HexColor("#EFF6FF"))
            for x in [115, 225, 335, 415]:
                self._arrow(c, x, 111, x + 15, 111)
            c.setFont("Helvetica", 8)
            c.setFillColor(colors.HexColor("#475569"))
            c.drawString(
                35,
                55,
                "Credenciales -> rate limit -> sesión -> access token -> validación de usuario, jti, exp y rol",
            )
        elif self.kind == "ticket":
            labels = [
                ("Cliente", 20, 95),
                ("TicketService", 140, 95),
                ("TicketRepository", 270, 95),
                ("SlaService", 400, 130),
                ("NotificationService", 400, 60),
            ]
            for label, x, y in labels:
                self._box(
                    c,
                    x,
                    y,
                    105,
                    30,
                    label,
                    colors.HexColor("#EFF6FF" if "Service" not in label else "#F8FAFC"),
                )
            self._arrow(c, 125, 110, 140, 110)
            self._arrow(c, 245, 110, 270, 110)
            self._arrow(c, 375, 110, 400, 145)
            self._arrow(c, 375, 105, 400, 75)
            c.setFont("Helvetica", 8)
            c.setFillColor(colors.HexColor("#475569"))
            c.drawString(
                22,
                45,
                "Se crean ticket, SLA, historial y notificación dentro de la misma operación de servicio",
            )
        else:
            nodes = [
                ("users", 20, 110),
                ("roles", 20, 45),
                ("tickets", 170, 110),
                ("ticket_comments", 330, 150),
                ("ticket_history", 330, 85),
                ("faqs", 170, 25),
                ("conversations", 470, 110),
            ]
            for label, x, y in nodes:
                self._box(c, x, y, 105, 28, label, colors.HexColor("#EFF6FF"))
            self._arrow(c, 125, 124, 170, 124)
            self._arrow(c, 72, 110, 72, 73)
            self._arrow(c, 275, 124, 330, 164)
            self._arrow(c, 275, 124, 330, 99)
            self._arrow(c, 275, 39, 470, 124)
        c.restoreState()


def p(text: str, style: ParagraphStyle) -> Paragraph:
    safe = html.escape(str(text)).replace("\n", "<br/>")
    return Paragraph(safe, style)


def pdf_table(
    headers: list[str],
    rows: list[list[object]],
    widths: list[float],
    styles: dict[str, ParagraphStyle],
) -> Table:
    data = [[p(header, styles["table_header"]) for header in headers]]
    data += [[p(value, styles["table_cell"]) for value in row] for row in rows]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E3A5F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F8FAFC")],
                ),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def build_pdf(
    routes: list[Route], models: list[dict[str, Any]], counts: dict[str, int]
) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="CoverTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=26,
            leading=31,
            textColor=colors.HexColor("#12355B"),
            alignment=TA_CENTER,
            spaceAfter=14,
        )
    )
    styles.add(
        ParagraphStyle(
            name="CoverSub",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#475569"),
            alignment=TA_CENTER,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H1x",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=21,
            textColor=colors.HexColor("#12355B"),
            spaceBefore=16,
            spaceAfter=8,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            name="H2x",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#1E3A5F"),
            spaceBefore=10,
            spaceAfter=5,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Bodyx",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            spaceAfter=6,
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Smallx",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=7.2,
            leading=9,
            textColor=colors.HexColor("#475569"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="table_header",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=6.6,
            leading=8,
            textColor=colors.white,
        )
    )
    styles.add(
        ParagraphStyle(
            name="table_cell",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=6.4,
            leading=8,
            textColor=colors.HexColor("#0F172A"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="Codex",
            parent=styles["Code"],
            fontName="Courier",
            fontSize=6.4,
            leading=8,
            backColor=colors.HexColor("#F1F5F9"),
            borderColor=colors.HexColor("#CBD5E1"),
            borderWidth=0.3,
            borderPadding=5,
            spaceAfter=7,
        )
    )

    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=16 * mm,
        bottomMargin=15 * mm,
        title="Documentación oficial del backend",
        author="Auditoría automática del repositorio",
    )
    story: list[Flowable] = []
    story.extend(
        [
            Spacer(1, 35 * mm),
            Paragraph("DOCUMENTACIÓN OFICIAL DEL BACKEND", styles["CoverTitle"]),
            Paragraph("Ticket Management API", styles["CoverSub"]),
            Spacer(1, 8 * mm),
            Paragraph(
                "Documento técnico basado exclusivamente en el código fuente real",
                styles["CoverSub"],
            ),
            Spacer(1, 18 * mm),
            DiagramFlowable("architecture", 500, 170),
            Spacer(1, 14 * mm),
            Paragraph(
                f"Versión del proyecto: 0.1.0 | Fecha: {date.today().isoformat()}",
                styles["CoverSub"],
            ),
            Paragraph(
                "Prototipo académico independiente. No es un sistema oficial bancario.",
                styles["CoverSub"],
            ),
            PageBreak(),
        ]
    )
    story += [
        Paragraph("Índice y resumen ejecutivo", styles["H1x"]),
        p(
            f"Este documento cubre {counts['routes']} endpoints, {counts['entities']} entidades, {counts['repositories']} repositorios, {counts['services']} archivos de servicio y {counts['test_cases']} casos de prueba encontrados. La documentación Markdown adjunta contiene el detalle textual completo y la trazabilidad por archivo.",
            styles["Bodyx"],
        ),
        pdf_table(
            ["Métrica", "Resultado"],
            [
                ["Módulos", counts["modules"]],
                ["Endpoints", counts["routes"]],
                ["Entidades", counts["entities"]],
                ["Repositorios", counts["repositories"]],
                ["Servicios", counts["services"]],
                ["Roles", 3],
                ["Integraciones activas", 0],
                ["Puntos opcionales", 1],
                [
                    "Pruebas",
                    f"{counts['test_cases']} casos en {counts['test_files']} archivos",
                ],
            ],
            [180, 330],
            styles,
        ),
        PageBreak(),
    ]
    story += [
        Paragraph("1. Introducción y alcance", styles["H1x"]),
        p(
            "La documentación fue generada inspeccionando app, migrations, tests y archivos de configuración del repositorio. No se incluyen funcionalidades futuras ni afirmaciones sin respaldo en el código.",
            styles["Bodyx"],
        ),
        Paragraph("2. Descripción general", styles["H1x"]),
        p(
            "El backend es un monolito modular FastAPI para gestión de tickets, con usuarios, autenticación JWT, conocimiento FAQ, chatbot determinista, SLA, adjuntos, notificaciones, reportes y auditoría. El README lo identifica como prototipo académico independiente.",
            styles["Bodyx"],
        ),
        Paragraph("3. Tecnologías utilizadas", styles["H1x"]),
        pdf_table(
            ["Tecnología", "Versión", "Función"],
            [
                ["Python", ">=3.11", "Lenguaje"],
                ["FastAPI", ">=0.115,<1.0", "API REST/OpenAPI"],
                ["SQLModel", ">=0.0.22,<1.0", "Persistencia"],
                ["MySQL", "8.4 Docker", "Base de datos"],
                ["Alembic", ">=1.13,<2.0", "Migraciones"],
                ["PyJWT", ">=2.9,<3.0", "JWT HS256"],
                ["pwdlib[argon2]", ">=0.2,<1.0", "Contraseñas"],
                ["pytest/httpx/ruff", "dev", "Calidad y pruebas"],
            ],
            [145, 105, 260],
            styles,
        ),
        PageBreak(),
    ]
    story += [
        Paragraph("4. Arquitectura", styles["H1x"]),
        p(
            "La cadena observada es router/controller -> service -> repository -> model/SQLModel -> MySQL. Las dependencias de sesión, autenticación y roles se inyectan con Annotated y Depends.",
            styles["Bodyx"],
        ),
        DiagramFlowable("architecture", 500, 170),
        Paragraph("5. Organización y módulos", styles["H1x"]),
        pdf_table(
            ["Módulo", "Responsabilidad", "Componentes"],
            module_rows(),
            [70, 210, 230],
            styles,
        ),
        PageBreak(),
    ]
    story += [
        Paragraph("6. Flujos principales", styles["H1x"]),
        Paragraph("Autenticación", styles["H2x"]),
        DiagramFlowable("auth", 500, 150),
        Paragraph("Creación de ticket", styles["H2x"]),
        DiagramFlowable("ticket", 500, 150),
        Paragraph("Modelo entidad-relación", styles["H2x"]),
        DiagramFlowable("er", 500, 190),
        PageBreak(),
    ]
    story += [
        Paragraph("7. API REST - resumen", styles["H1x"]),
        p(
            "Las rutas siguientes fueron extraídas de decoradores FastAPI. Los roles y descripciones se cotejaron con servicios y dependencias.",
            styles["Bodyx"],
        ),
        pdf_table(
            ["Método", "Endpoint", "Auth", "Rol"],
            [
                [
                    r.method,
                    r.path,
                    META.get(route_key(r), default_meta(r)).auth,
                    META.get(route_key(r), default_meta(r)).role,
                ]
                for r in routes
            ],
            [45, 210, 80, 188],
            styles,
        ),
        PageBreak(),
    ]
    story.append(Paragraph("7. API REST - detalle de endpoints", styles["H1x"]))
    for route in routes:
        meta = META.get(route_key(route), default_meta(route))
        story.extend(
            [
                Paragraph(f"{route.method} {route.path}", styles["H2x"]),
                p(meta.description, styles["Bodyx"]),
                pdf_table(
                    ["Aspecto", "Detalle"],
                    [
                        ["Auth", meta.auth],
                        ["Rol", meta.role],
                        ["Entrada", meta.request],
                        ["Respuesta", f"HTTP {route.status}; {meta.response}"],
                        ["Errores", meta.errors],
                        ["Código", f"{route.file}:{route.line} ({route.function})"],
                    ],
                    [80, 443],
                    styles,
                ),
                Spacer(1, 4),
            ]
        )
    story.append(PageBreak())
    story += [
        Paragraph("8. Seguridad y permisos", styles["H1x"]),
        p(
            "HTTPBearer valida claims exp, iat, sub y type. Los tokens nuevos usan jti vinculado a auth_sessions; logout revoca la sesión. Password reset y email verification almacenan solo hashes y son de un solo uso. La redacción global elimina claves sensibles y el chatbot aplica guardrails contra inyección, secretos, scripts, fuentes inválidas y contexto insuficiente.",
            styles["Bodyx"],
        ),
        DiagramFlowable("auth", 500, 150),
        pdf_table(
            ["Rol", "Alcance"],
            [
                [
                    "CLIENTE",
                    "Tickets propios, chatbot, perfil/preferencias, feedback y notificaciones propias.",
                ],
                ["ASESOR", "Tickets asignados, colas operativas y SLA autorizado."],
                [
                    "SUPERVISOR",
                    "Operación global, conocimiento, SLA, reportes, exportaciones y auditoría.",
                ],
            ],
            [95, 428],
            styles,
        ),
        Paragraph("9. Persistencia", styles["H1x"]),
        p(
            f"SQLModel registra {counts['entities']} tablas y Alembic contiene 17 revisiones hasta 0017_chatbot_ai_trace.",
            styles["Bodyx"],
        ),
        pdf_table(
            ["Modelo", "Tabla", "Campos principales", "FK"],
            entity_rows(models),
            [80, 90, 245, 108],
            styles,
        ),
        PageBreak(),
    ]
    story += [
        Paragraph("10. Reglas, validaciones e integraciones", styles["H1x"]),
        p(
            "Las reglas centrales cubren roles, transiciones de tickets, editorial FAQ, cuotas chatbot/adjuntos, SLA, deduplicación, redacción, exportación CSV y acceso por propiedad/asignación. Las validaciones Pydantic y de servicios producen principalmente 401, 403, 404, 409, 413, 415 y 422 según el flujo.",
            styles["Bodyx"],
        ),
        pdf_table(
            ["Integración", "Estado", "Comportamiento"],
            [
                [
                    "OpenAI Responses API",
                    "Opcional",
                    "store=false, salida estructurada, FAQ autorizadas, timeout y fallback controlado.",
                ],
                [
                    "Gateway IA HTTP JSON",
                    "Opcional",
                    "Bearer API key, timeout, contexto FAQ autorizado, fuentes obligatorias y fallback.",
                ],
                ["Correo", "Noop", "No envía ni guarda tokens."],
                ["Notificaciones externas", "Noop", "No hace red; persiste in-app."],
                [
                    "Archivos",
                    "Local",
                    "ATTACHMENT_STORAGE_DIR, path traversal bloqueado.",
                ],
            ],
            [130, 85, 308],
            styles,
        ),
        Paragraph("11. Configuración y ejecución", styles["H1x"]),
        p(
            "Configurar .env desde .env.example, levantar MySQL con Docker Compose, aplicar alembic upgrade head, cargar seed y ejecutar fastapi dev app/main.py. No se reproducen secretos reales: el PDF usa máscaras como ********.",
            styles["Bodyx"],
        ),
        pdf_table(
            ["Grupo", "Propósito", "Ejemplo seguro"],
            [[r[0], r[2], r[3]] for r in config_rows()],
            [160, 225, 138],
            styles,
        ),
        PageBreak(),
    ]
    story += [
        Paragraph("12. Pruebas, pendientes y conclusión", styles["H1x"]),
        p(
            f"La auditoría ejecutó pytest: 219 passed, 2 warnings; ruff check y ruff format --check pasan. alembic check quedó bloqueado por MySQL no disponible en localhost:3307. Se encontraron {counts['test_cases']} casos de prueba en {counts['test_files']} archivos.",
            styles["Bodyx"],
        ),
        p(
            "Pendientes observados: proveedores Noop de correo/notificaciones; IA opcional deshabilitada; XLSX no disponible; SLA sin scheduler; no existen acciones masivas. El health check es estático y no verifica la base.",
            styles["Bodyx"],
        ),
        p(
            "Conclusión: el repositorio implementa un prototipo modular y funcional de gestión de tickets con trazabilidad, soporte FAQ/chatbot y operación interna. Las integraciones externas son puntos de extensión, no servicios activos por defecto.",
            styles["Bodyx"],
        ),
        Paragraph("Entregables", styles["H2x"]),
        p(
            "Markdown: docs/DOCUMENTACION_OFICIAL_BACKEND.md. PDF: output/pdf/DOCUMENTACION_OFICIAL_BACKEND.pdf.",
            styles["Bodyx"],
        ),
    ]

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.line(document.leftMargin, 11 * mm, A4[0] - document.rightMargin, 11 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(
            document.leftMargin,
            7 * mm,
            "Ticket Management API - documentación basada en código fuente",
        )
        canvas.drawRightString(
            A4[0] - document.rightMargin, 7 * mm, f"Página {document.page}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main() -> None:
    routes = extract_routes()
    models = extract_model_inventory()
    counts = actual_counts(routes, models)
    markdown = build_markdown(routes, models, counts)
    DOCS.mkdir(parents=True, exist_ok=True)
    diagram_dir = DOCS / "diagrams"
    diagram_dir.mkdir(parents=True, exist_ok=True)
    for name, source in mermaid_diagrams().items():
        (diagram_dir / f"{name}.mmd").write_text(
            source.strip() + "\n", encoding="utf-8"
        )
    MD_PATH.write_text(markdown, encoding="utf-8")
    build_pdf(routes, models, counts)
    print(f"Módulos encontrados: {counts['modules']}")
    print(f"Endpoints totales: {counts['routes']}")
    print(f"Entidades: {counts['entities']}")
    print(f"Repositories: {counts['repositories']}")
    print(f"Servicios: {counts['services']}")
    print("Roles: 3")
    print(
        "Integraciones externas activas: 0 (1 puerto IA opcional; 2 adaptadores disponibles)"
    )
    print(
        f"Pruebas: {counts['test_cases']} casos recogidos por Pytest; {counts['test_functions']} funciones en {counts['test_files']} archivos"
    )
    print(f"Diagramas Mermaid: {diagram_dir} ({len(mermaid_diagrams())} fuentes)")
    print(f"Archivos generados: {MD_PATH}; {PDF_PATH}")
    print(f"Ubicación del PDF final: {PDF_PATH}")


if __name__ == "__main__":
    main()
