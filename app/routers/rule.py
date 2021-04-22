import time

import elasticsearch
from fastapi import APIRouter, Request
from fastapi import HTTPException, Depends

from .. import config
from ..domain.rule import Rule
from ..errors.errors import NullResponseError, RecordNotFound, convert_exception_to_json
from ..globals.authentication import get_current_user
from ..globals.context_server import context_server_via_uql
from ..globals.elastic_client import elastic_client
from ..routers.misc import query
from ..storage.actions.rule import upsert_rule
from ..storage.helpers import update_record

router = APIRouter(
    prefix="/rule",
    # dependencies=[Depends(get_current_user)]
)


@router.get("/{id}")
async def rule_get(id: str,
                   uql=Depends(context_server_via_uql),
                   elastic=Depends(elastic_client)):
    q = f"SELECT RULE WHERE id=\"{id}\""

    try:
        response_tuple = uql.select(q)
        result = uql.respond(response_tuple)

        if not result or 'list' not in result or not result['list']:
            raise RecordNotFound("Item not found")

        try:
            elastic_result = elastic.get(config.index['rules'], id)
        except elasticsearch.exceptions.NotFoundError:
            elastic_result = RecordNotFound()
            elastic_result.message = "UQL statement not found."

        result = {
            'unomi': result['list'][0],
        }

        if not isinstance(elastic_result, RecordNotFound):
            result['uql'] = elastic_result['_source']

        return result

    except NullResponseError as e:
        raise HTTPException(status_code=e.response_status, detail=convert_exception_to_json(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=convert_exception_to_json(e))


@router.delete("/{id}")
async def rule_delete(id: str,
                      uql=Depends(context_server_via_uql),
                      elastic=Depends(elastic_client)):

    q = f"DELETE RULE \"{id}\""
    response_tuple = uql.delete(q)
    result = uql.respond(response_tuple)

    try:
        index = config.index['rules']
        elastic.delete(index, id)
    except elasticsearch.exceptions.NotFoundError:
        # todo logging
        print("Record {} not found in elastic.".format(id))

    return result


@router.put("/{id}")
async def rule_update(id: str,
                      request: Request,
                      elastic=Depends(elastic_client)):
    index = config.unomi_index['rule']
    record = await request.json()
    return update_record(elastic, index, id, record)


@router.post("/")
async def create_query(rule: Rule, uql=Depends(context_server_via_uql), elastic=Depends(elastic_client)):
    if not rule.name:
        raise HTTPException(status_code=412, detail=[{"msg": "Empty name.", "type": "Missing data"}])

    if not rule.scope:
        raise HTTPException(status_code=412, detail=[{"msg": "Empty scope.", "type": "Missing data"}])

    if not rule.condition:
        raise HTTPException(status_code=412, detail=[{"msg": "Empty condition.", "type": "Missing data"}])

    if not rule.actions:
        raise HTTPException(status_code=412, detail=[{"msg": "Empty actions.", "type": "Missing data"}])

    actions = ",".join(rule.actions)
    if 'uql' not in rule.tags:
        rule.tags += ["uql"]

    tags = '"{}"'.format('","'.join(rule.tags))

    q = f"CREATE RULE WITH TAGS [{tags}] \"{rule.name}\" DESCRIBE \"{rule.description.strip()}\" IN SCOPE \"{rule.scope}\" " + \
        f"WHEN {rule.condition} THEN {actions}"

    print(q)
    unomi_result = query(q, uql)
    upserted_records, errors = upsert_rule(elastic, q, rule)

    return unomi_result


@router.post("/query/json")
async def get_unomi_query(request: Request, uql=Depends(context_server_via_uql)):
    q = await request.body()
    q = q.decode('utf-8')
    _, url, method, body, _ = uql.get_unomi_query(q)
    return url, method, body


@router.post("/select")
async def rule_select(request: Request, uql=Depends(context_server_via_uql)):
    try:
        q = await request.body()
        q = q.decode('utf-8')
        if q:
            q = f"SELECT RULE WHERE {q}"
        else:
            q = "SELECT RULE LIMIT 20"

        response_tuple = uql.select(q)
        result = uql.respond(response_tuple)
        return result['list'] if 'list' in result else []
    except NullResponseError as e:
        raise HTTPException(status_code=e.response_status, detail=convert_exception_to_json(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=convert_exception_to_json(e))


@router.get("/stats/{id}")
async def rule_stats(id: str,
                   elastic=Depends(elastic_client)):
    try:
        index = config.unomi_index['rulestats']
        result = elastic.get(index, id)
        return result['_source']
    except elasticsearch.exceptions.TransportError as e:
        raise HTTPException(status_code=e.status_code, detail=convert_exception_to_json(e))
