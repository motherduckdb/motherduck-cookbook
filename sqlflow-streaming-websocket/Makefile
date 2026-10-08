IMAGE ?= turbolytics/sql-flow:v1.1.0
MSGS  ?= 2000

.PHONY: run stream validate clean

# Stream a bounded number of events, then stop. Start here.
run:
	docker run --rm --env-file .env -v "$(PWD)":/conf $(IMAGE) \
	  run /conf/pipeline.yml --max-msgs=$(MSGS)

# Stream until interrupted.
stream:
	docker run --rm --env-file .env -v "$(PWD)":/conf $(IMAGE) \
	  run /conf/pipeline.yml

# Check the config against the engine's schema without connecting to anything.
validate:
	docker run --rm -v "$(PWD)":/conf $(IMAGE) config validate /conf/pipeline.yml

# Remove the table this recipe created.
clean:
	@echo "DROP TABLE IF EXISTS my_db.bluesky_posts;" | duckdb "md:"
