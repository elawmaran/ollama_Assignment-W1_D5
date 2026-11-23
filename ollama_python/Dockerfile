FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ui_theme.py streamlit_auth.py style.css warm.py alembic.ini ./
COPY .streamlit .streamlit
COPY elaw_core elaw_core
COPY auth_api auth_api
COPY views views
COPY migrations migrations
RUN useradd --create-home --uid 10001 elaw
USER elaw
EXPOSE 8501 8000
# Default: the chat app. The auth service overrides the command (see docker-compose.yml).
CMD ["sh", "-c", "python warm.py & exec streamlit run app.py --server.address=0.0.0.0 --server.port=8501"]
