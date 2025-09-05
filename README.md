## Avisador Oportunidades Cripto (MVP)

Pequeño servicio que monitoriza pares en DEX (p. ej. PancakeSwap en BSC) usando la API pública de Dexscreener y envía alertas a Telegram cuando detecta picos tempranos de volumen, momentum y desequilibrio entre compras/ventas.

### Requisitos
- Python 3.10+
- Cuenta y bot de Telegram (token del bot y chat id)

### Instalación rápida (Windows)
1. Crear y activar entorno virtual (opcional):
   - PowerShell:
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```
2. Instalar dependencias:
   ```powershell
   pip install -r requirements.txt
   ```
3. Copiar `config.example.yaml` a `config.yaml` y editar con tus claves y tokens a vigilar.

### Uso
- Ejecutar una sola iteración (prueba):
  ```powershell
  python main.py --once
  ```
- Ejecutar en bucle:
  ```powershell
  python main.py
  ```

### Telegram
1. Crea un bot con @BotFather y copia el token.
2. Obtén tu chat id: escribe al bot y luego abre `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. En `config.yaml` pon:
   ```yaml
   telegram:
     enabled: true
     bot_token: "TU_TOKEN"
     chat_id: "TU_CHAT_ID"
   ```

### Supabase (opcional)
1. Crea un proyecto en Supabase y habilita la API REST.
2. Ejecuta `supabase.sql` en el editor SQL del panel para crear la tabla `alerts` y políticas.
3. En `config.yaml` añade:
   ```yaml
   supabase:
     enabled: true
     url: "https://YOUR-PROJECT.supabase.co"
     anon_key: "YOUR_ANON_KEY"
     table_alerts: "alerts"
   ```
4. Al dispararse una alerta, se insertará un registro en `alerts` vía REST.

### Configuración
Ver `config.example.yaml` para todos los parámetros disponibles. Los principales:
- `watchlist`: contratos o pares a monitorizar (por ahora Dexscreener por `pairAddress` o `tokenAddress`).
- `poll_seconds`: frecuencia de consulta.
- `thresholds`: umbrales para volumen, momentum y ratios de compras/ventas.
- `telegram`: token y chat id para enviar alertas (opcionales si solo quieres logs).
- `discovery`: activa el modo de descubrimiento automático y define filtros (redes, liquidez mínima, volumen m5, etc.).

### Nota
Este MVP consulta únicamente Dexscreener y calcula señales en ventanas cortas (1–5m). No es consejo financiero.


