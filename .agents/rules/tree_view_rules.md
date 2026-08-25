---
description: Reglas de inicialización y diseño para árboles jerárquicos (Tree Views)
globs: "**/*.tsx,**/*.jsx,**/*.py"
always_on: true
---

# Reglas para Componentes de Árbol Jerárquico (Tree Views)

1. **Estado Inicial por Defecto (Colapsado / Cerrado)**:
   - Todo árbol jerárquico (ej: Control de Secciones, Control de Permisos, categorizadores, exploradores de archivos) **SIEMPRE** debe inicializarse **cerrado / colapsado por defecto**.
   - El estado de nodos expandidos debe inicializarse vacío (`const [expandedCodes, setExpandedCodes] = useState<Record<string, boolean>>({});`) y evaluar `expandedCodes[code] ?? false`.
   - La expansión debe ser siempre por acción manual del usuario.

2. **Estilo Visual Google AI Studio**:
   - Tema oscuro (#191919 / #1e1e1e / #18181a).
   - Líneas guía conectoras de jerarquía punteadas (`border-l border-dashed border-[#444448]`) con ramas horizontales (`before:border-t before:border-dashed`).
   - Flecha de expansión `▶` / `▼` animada con rotación a la izquierda del checkbox o ícono.
   - Ícono de carpeta (`Folder`/`FolderOpen`) para contenedores e ícono de documento (`FileText`) para hojas.
