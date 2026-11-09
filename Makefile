# 멀티봇 허브 개발 명령. 비밀값은 .env 에 둔다(커밋 금지, .env.example 참고).
# .env 는 make 로 읽지 않는다: make 는 #·$·따옴표를 compose 와 다르게 해석한다.
# 테스트는 hub/tests/envfile.py 가 compose 규칙으로 .env 를 읽는다.

COMPOSE := docker compose
# 계약 테스트용: 허브 웹훅을 기록용 수신기로 돌리는 오버레이를 얹는다
COMPOSE_CONTRACT := $(COMPOSE) -f docker-compose.yml -f docker-compose.override.yml -f adapters/tests/contract/compose.yml
JSON2TS := pnpm dlx json-schema-to-typescript@15.0.4
# 평가 전용 스택(운영 스택과 다른 compose 프로젝트). runner/eval/compose.yml 참고
COMPOSE_EVAL := $(COMPOSE) -p multibot-eval -f runner/eval/compose.yml
EVAL_TOKEN := runner/eval/out/token
# UI E2E 전용 스택(운영·평가와 다른 compose 프로젝트). e2e/compose.yml 참고
COMPOSE_E2E := $(COMPOSE) -p multibot-e2e --env-file e2e/out/env -f e2e/compose.yml
RAND_HEX = head -c $(1) /dev/urandom | od -An -tx1 | tr -d ' \n'

.PHONY: up down logs ps test test-hub test-runner test-adapters test-web eval e2e types admin runner-login

up:  ## 로컬 스택 기동 (postgres, hub-api, runner, web, mock-a/b/c)
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

test: test-hub test-runner test-adapters test-web  ## hub·runner·adapters·web 전부

test-hub:  ## 허브 단위 테스트. 로컬 postgres(5433)의 multibot_test DB 를 쓴다(.env 의 계정)
	$(COMPOSE) up -d --wait postgres
	cd hub && uv run pytest -q
	@echo "hub tests done"

test-runner:  ## 러너 단위 테스트. 가짜 claude 와 stream-json 고정 샘플로 돌린다(실제 claude·로그인 불필요)
	cd runner && uv run pytest -q
	@echo "runner tests done"

test-adapters:  ## 어댑터 단위 테스트 + Mock 3개·Vooy(가짜 기기) 계약 테스트(docs/03-adapters.md 11장)
	cd adapters && uv run --group android pytest -q tests/unit
	$(COMPOSE_CONTRACT) up -d --build --wait webhook-sink mock-a mock-b mock-c vooy-fake
	cd adapters && uv run pytest -q tests/contract -n 4 --dist loadgroup
	@echo "adapter tests done"

test-web:  ## 웹 타입 검사 + 스토어 리듀서 스냅샷·말풍선·입력창 테스트(vitest)
	cd web && pnpm install --frozen-lockfile && pnpm typecheck && pnpm test
	@echo "web tests done"

eval:  ## T1~T14 평가: 실제 claude -p 를 Mock 으로 각 5회. 러너 로그인 필요(make runner-login). 예: make eval ARGS="--cases T3,T10 --n 1"
	@test -s $(EVAL_TOKEN) || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' > $(EVAL_TOKEN)
	$(COMPOSE_EVAL) up -d --build --wait
	$(COMPOSE_EVAL) run --rm eval python -m eval $(ARGS); rc=$$?; $(COMPOSE_EVAL) down; exit $$rc

# make test 에 넣지 않는다: 실제 claude -p 를 돌려 러너 로그인(make runner-login)과 구독 사용량이 필요하고,
# LLM 응답이 매번 달라 결정적이지 않다(make eval 과 같은 이유). P1 게이트·프롬프트 수정 뒤에 돌린다.
e2e:  ## UI E2E 5개(Playwright): Mock + 실제 허브·러너. 예: make e2e ARGS="-g T8", 스택 유지는 E2E_KEEP=1
	@mkdir -p e2e/out/bots
	@test -s e2e/out/token || $(call RAND_HEX,24) > e2e/out/token
	@test -s e2e/out/env || printf 'E2E_POSTGRES_PASSWORD=%s\nE2E_HUB_SECRET_KEY=%s\n' "$$($(call RAND_HEX,16))" "$$($(call RAND_HEX,32))" > e2e/out/env
	@cp e2e/bots/default.yaml e2e/out/bots/bots.yaml
	$(COMPOSE_E2E) up -d --build --wait
	(cd e2e && pnpm install --frozen-lockfile && pnpm exec playwright test $(ARGS)); rc=$$?; \
	  if [ -z "$(E2E_KEEP)" ]; then $(COMPOSE_E2E) down; fi; exit $$rc

types:  ## hub/app/events.py -> web/src/types/events.ts
	uv run -q --project hub python scripts/gen_types.py web/src/types/events.schema.json
	$(JSON2TS) -i web/src/types/events.schema.json -o web/src/types/events.ts --unreachableDefinitions --additionalProperties false --bannerComment ""
	uv run -q --project hub python scripts/gen_types.py --banner web/src/types/events.ts

admin:  ## 운영자 계정 관리. 예: make admin ARGS="create-operator --username me"
	$(COMPOSE) exec hub-api python -m app.admin $(ARGS)

runner-login:  ## 러너 컨테이너의 Claude Code 로그인(운영자가 직접, 최초 1회). 로그인 정보는 runner-claude 볼륨에만 남는다
	$(COMPOSE) exec -it runner claude auth login
