# 1단계: Vite 빌드. 2단계: nginx 가 빌드 결과를 서빙하고 /api·/ws 를 허브로 프록시한다.
FROM node:22.11.0-alpine AS build
WORKDIR /app
# corepack 는 이 node 버전의 서명 키가 낡아 실패한다. pnpm 을 고정 버전으로 직접 설치
RUN npm install -g pnpm@10.15.0
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN pnpm build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/
