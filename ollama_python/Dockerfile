FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py style.css warm.py ./
COPY .streamlit .streamlit
EXPOSE 8501
CMD ["sh", "-c", "python warm.py & exec streamlit run app.py --server.address=0.0.0.0 --server.port=8501"]
