FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt waitress
COPY . .
ENV DATABASE_URL=sqlite:////app/data/projects.db FLASK_HOST=0.0.0.0
EXPOSE 5000
# Build the database on first start, then serve with a production WSGI server.
CMD ["sh", "-c", "python main.py sync && python -m waitress --host=0.0.0.0 --port=5000 --call src.dashboard:create_app"]
