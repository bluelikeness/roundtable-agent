# 멀티봇 허브 개발 명령. 비밀값은 .env 에 둔다(커밋 금지, .env.example 참고).
# .env 는 make 로 읽지 않는다: make 는 #·$·따옴표를 compose 와 다르게 해석한다.
# 테스트는 hub/tests/envfile.py 가 compose 규칙으로 .env 를 읽는다.

COMPOSE := docker compose
# 계약 테스트용: 허브 웹훅을 기록용 수신기로 돌리는 오버레이를 얹는다
COMPOSE_CONTRACT := $(COMPOSE) -f docker-compose.yml -f docker-compose.override.yml -f adapters/tests/contract/compose.yml
JSON2TS := pnpm dlx json-schema-to-typescript@15.0.4

.PHONY: up down logs ps test test-hub test-adapters eval types admin

up:  ## 로컬 스택 기동 (postgres, hub-api, web, mock-a/b/c)
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test: test-hub test-adapters  ## hub·adapters·web 전부 (web 은 P1-5 에서 추가)

test-hub:  ## 허브 단위 테스트. 로컬 postgres(5433)의 multibot_test DB 를 쓴다(.env 의 계정)
	$(COMPOSE) up -d --wait postgres
	cd hub && uv run pytest -q
	@echo "hub tests done"

test-adapters:  ## 어댑터 단위 테스트 + Mock 3개 계약 테스트(docs/03-adapters.md 11장)
	cd adapters && uv run pytest -q tests/unit
	$(COMPOSE_CONTRACT) up -d --build --wait webhook-sink mock-a mock-b mock-c
	cd adapters && uv run pytest -q tests/contract -n 3 --dist loadgroup
	@echo "adapter tests done"

eval:  ## T1~T14 평가 (P1-4 이후)
	@echo "eval 은 P1-4 에서 구현합니다" && exit 1

types:  ## hub/app/events.py -> web/src/types/events.ts
	uv run -q --project hub python scripts/gen_types.py web/src/types/events.schema.json
	$(JSON2TS) -i web/src/types/events.schema.json -o web/src/types/events.ts --unreachableDefinitions --additionalProperties false --bannerComment ""
	uv run -q --project hub python scripts/gen_types.py --banner web/src/types/events.ts

admin:  ## 운영자 계정 관리. 예: make admin ARGS="create-operator --username me"
	$(COMPOSE) exec hub-api python -m app.admin $(ARGS)
