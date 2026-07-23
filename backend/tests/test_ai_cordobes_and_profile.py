"""Tests for Cordobés dialect prompt building, catalog synonym search, and client profile extraction."""
import pytest
from ai.prompts import build_system_prompt
from ai.catalog_search import search_catalog, format_catalog_for_llm
from tests.test_simulate_inbound import _FakeDB


def test_cordobes_dialect_system_prompt():
    prompt = build_system_prompt(
        tone="amigable",
        company_context="Inmobiliaria Córdoba",
        response_instructions="Brindar info clara",
        faqs=[],
        handoff_rules="Derivar si enojado",
        tone_dialect="cordobes",
        response_length_limit="conciso",
        writing_rules={"only_closing_punctuation": True, "allow_slang": True, "custom_rules_text": "Sin mayúsculas sostenidas"},
        company_workflow_steps=["Saludar al cliente", "Consultar presupuesto", "Ofrecer catálogo"],
        custom_client_fields=[{"key": "presupuesto", "label": "Presupuesto", "description": "USD"}]
    )

    assert "persona de Córdoba, Argentina" in prompt
    assert "REGLA DE ORTOGRAFÍA OBLIGATORIA: Usá ÚNICAMENTE los signos de cierre" in prompt
    assert "LONGITUD DE RESPUESTA: Mantené tus respuestas breves" in prompt
    assert "Pasos del Proceso de Atención de la Empresa" in prompt
    assert "Paso 1: Saludar al cliente" in prompt
    assert "Ficha Personalizada del Cliente — Campos a detectar y completar:" in prompt
    assert "extracted_client_profile" in prompt


@pytest.mark.anyio
async def test_catalog_synonym_search():
    fake_db = _FakeDB()
    await fake_db.products.insert_one({
        "id": "prod_1",
        "name": "Departamento 2 Dormitorios Nueva Córdoba",
        "sku": "DEP-2D-NC",
        "category": "Inmuebles",
        "price": 85000,
        "currency": "USD",
        "keywords": ["depto", "2 amb", "nueva cordoba"],
        "aliases": ["dpto 2 dormitorios", "depto 2 amb"],
        "description": "Excelente depto frente a la plaza",
        "active": True,
        "deleted_at": None,
    })

    # Search using alias "2 amb"
    res = await search_catalog(fake_db, "depto 2 amb")
    assert len(res) >= 1
    assert res[0]["name"] == "Departamento 2 Dormitorios Nueva Córdoba"

    formatted = format_catalog_for_llm(res)
    assert "Sinónimos / Términos clave: depto, 2 amb, nueva cordoba, dpto 2 dormitorios, depto 2 amb" in formatted
