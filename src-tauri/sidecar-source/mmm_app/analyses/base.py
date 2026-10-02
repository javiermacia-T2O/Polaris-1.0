"""
CONTRATO DE PLUGIN
==================

Cada script en analyses/ debe definir:

    NAME: str           # Nombre visible en el menú
    DESCRIPTION: str    # Breve descripción
    CATEGORY: str       # Categoría (opcional, por defecto "General")

    def run(df: pd.DataFrame, **kwargs) -> dict:
        '''
        Devuelve un dict donde:
          - clave: título de la sección
          - valor: DataFrame, Series, str, número o lista
        '''

Este archivo no se ejecuta: solo documenta.
"""