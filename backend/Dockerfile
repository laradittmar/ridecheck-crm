FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

# L4.7W4-F3: deployment identity is baked into the image at build time.
# It used to come from a compose default, `GIT_SHA: "${GIT_SHA:-d5f89b3}"`, which kept
# stamping a commit from three milestones earlier onto every outbound record — the ledger
# could not answer "which deployment sent this". An image knows what it was built from;
# a shell variable someone has to remember to export does not.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
