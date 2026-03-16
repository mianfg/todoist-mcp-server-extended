# Todoist MCP Server Extended + HTTP proxy
# Builds the MCP server and wraps it with mcp-streamablehttp-proxy to expose over HTTP
# (The original server is stdio-only; the proxy enables remote access via Streamable HTTP)

FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --ignore-scripts

COPY src ./src
COPY tsconfig.json .
RUN npm run build

# Runtime: Python + proxy + built Node app
FROM python:3.11-alpine

RUN apk add --no-cache nodejs npm

RUN pip install --no-cache-dir mcp-streamablehttp-proxy uvicorn httpx fastapi python-multipart

WORKDIR /app

COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package.json .
COPY --from=builder /app/node_modules ./node_modules
COPY auth_proxy.py .
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 3000

# Auth proxy (3000) validates Bearer token, forwards to MCP proxy (3001)
# TODOIST_API_TOKEN + MCP_ACCESS_TOKEN must be set at runtime
CMD ["./start.sh"]
