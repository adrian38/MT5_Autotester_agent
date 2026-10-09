"""Enter prepared sets into normal Discovery evaluation, without generating mutations."""
from __future__ import annotations
import base64
import json
from dataclasses import dataclass, field
import re
from datetime import datetime
from pathlib import Path

from manager_node_runtime import guided_batches as protocol
from .models import Seed, Variant


def _broker_execution_item(item, universe, symbol_map, api, *, strict=False):
    """Return active-universe spelling without changing batch identity."""
    mapped = api.apply_symbol_map(item['target_symbol'], symbol_map)
    normalized = api.normalize_set_symbol(mapped)
    matches = [symbol for symbol in sorted(universe) if symbol.casefold() == str(mapped).strip().casefold()]
    if strict:
        if len(matches) != 1:
            raise ValueError('No se puede resolver un nombre MT5 único en el universo del broker: '+str(mapped))
        exact = matches[0]
    elif len(matches) == 1:
        # Same name, broker's own casing: this is the spelling MT5 resolves.
        exact = matches[0]
    else:
        exact = next(
            (
                symbol
                for symbol in sorted(universe)
                if api.normalize_set_symbol(symbol) == normalized
            ),
            item['target_symbol'],
        )
    if exact == item['target_symbol']:
        return item
    mutation = dict(item['mutation'])
    # Only retargeting carries the instrument inside its mutation. A recovery
    # keeps the instrument and moves a numeric parameter instead.
    if item['mode'] == 'symbol_exploration' and mutation.get('kind') != 'symbol_recovery':
        mutation['new'] = exact
    return {**item, 'target_symbol': exact, 'mutation': mutation}


def _registered_parent_matches(source_raw, parent_raw, symbol_map, api):
    """Allow only the broker's equivalent ForceSymbol spelling to differ."""
    source_text = protocol.set_text(source_raw)
    parent_text = protocol.set_text(parent_raw)
    if source_text == parent_text:
        return True

    def symbol_and_masked(text):
        symbols = []
        masked = []
        for line in text.splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip().casefold() == 'forcesymbol':
                current, metadata_separator, metadata = value.partition('||')
                symbols.append(current.strip())
                value = '__BROKER_SYMBOL__' + (metadata_separator + metadata if metadata_separator else '')
                line = key + separator + value
            masked.append(line)
        return symbols, '\n'.join(masked)

    source_symbols, source_masked = symbol_and_masked(source_text)
    parent_symbols, parent_masked = symbol_and_masked(parent_text)
    if len(source_symbols) != 1 or len(parent_symbols) != 1 or source_masked != parent_masked:
        return False
    expected = api.apply_symbol_map(parent_symbols[0], symbol_map)
    return source_symbols[0].casefold() == str(expected).strip().casefold()


def _is_cross_broker_parent(item) -> bool:
    provenance = item.get('parent_provenance')
    return isinstance(provenance, dict) and provenance.get('kind') == 'cross_broker_final'


def _is_local_seed_parent(item) -> bool:
    provenance = item.get('parent_provenance')
    return isinstance(provenance, dict) and provenance.get('kind') == 'local_seed'


def _is_local_lineage_parent(item) -> bool:
    provenance = item.get('parent_provenance')
    return isinstance(provenance, dict) and provenance.get('kind') == 'local_candidate'


@dataclass
class _PreparedContext:
    """Universo, mapas y caches con los que se valida un lote preparado."""

    args: object
    memory: object
    api: object
    universe: object
    execution_universe: set
    disabled: object
    symbol_map: dict
    timeframes: object
    frozen: dict
    globals_: dict
    row_cache: dict = field(default_factory=dict)
    parent_cache: dict = field(default_factory=dict)

    def registered_row(self, item, recovery):
        """Fila local del padre declarado, segun sea recuperacion o positivo."""
        row_key = (recovery, item['parent_candidate_id'], item['period'] if recovery else None)
        if row_key in self.row_cache:
            return self.row_cache[row_key]
        if recovery:
            # Adapting partial progress has its own provenance: the parent is a
            # candidate this node already evaluated for a prepared batch, on the
            # same timeframe, that never reached an accepted final positive. The
            # protocol already proved the child keeps its parent's instrument.
            row = self.memory.conn.execute('''select c.set_path from candidates c join runs r on r.id=c.run_id
                where c.id=? and c.period=? and json_extract(case when json_valid(r.config_json)
                    then r.config_json else '{}' end,'$.prepared_batch_id') is not null
                and not exists (select 1 from candidate_final_tick_6m f
                                where f.candidate_id=c.id and f.status='accepted')''',
                (item['parent_candidate_id'], item['period'])).fetchone()
        else:
            row = self.memory.conn.execute('''select c.set_path from candidates c join candidate_final_tick_6m f
                on f.candidate_id=c.id where c.id=? and f.status='accepted' ''',
                (item['parent_candidate_id'],)).fetchone()
        self.row_cache[row_key] = row
        return row

    def registered_seed(self, item):
        """Return the current active, base-accepted seed declared by package v3."""
        seed_id = item['parent_provenance']['seed_id']
        row_key = ('seed', seed_id)
        if row_key not in self.row_cache:
            self.row_cache[row_key] = self.memory.conn.execute(
                '''select s.seed_path from seed_scores s where s.id=? and s.active=1
                   and s.status='accepted' and upper(s.symbol)=upper(?)
                   and s.period=? and s.family=? and upper(s.seed_path)=upper(?)''',
                (seed_id, item['target_symbol'], item['period'], item['family'],
                 item['root_seed'])).fetchone()
        return self.row_cache[row_key]

    def registered_lineage_candidate(self, item):
        """Return a base winner from a local lineage that has no final winner."""
        run_id = item['parent_provenance']['run_id']
        row_key = ('lineage', item['parent_candidate_id'], run_id)
        if row_key not in self.row_cache:
            self.row_cache[row_key] = self.memory.conn.execute(
                '''select c.set_path from candidates c where c.id=? and c.run_id=?
                   and c.status='accepted' and upper(c.target_symbol)=upper(?)
                   and c.period=? and c.family=? and not exists (
                       select 1 from candidate_final_tick_6m f
                       where f.candidate_id=c.id and f.status='accepted')''',
                (item['parent_candidate_id'], run_id, item['target_symbol'],
                 item['period'], item['family'])).fetchone()
        return self.row_cache[row_key]

    def verify_parent(self, row, parent) -> None:
        """Comprueba que el padre recibido es el set local registrado.

        Memories survive checkout moves (for example the RoboForex workspace
        moved from C: to G:). Resolve only known workspace subtrees below the
        current BASE_DIR, then keep the existing containment and byte checks.
        """
        api = self.api
        source = api.resolve_workspace_path(row[0], base_dir=api.BASE_DIR).resolve()
        if not source.is_relative_to(api.BASE_DIR.resolve()):
            raise ValueError('El padre recibido no coincide con el set local registrado')
        source_raw = self.parent_cache.get(source)
        if source_raw is None:
            source_raw = source.read_bytes()
            self.parent_cache[source] = source_raw
        if not _registered_parent_matches(source_raw, parent, self.symbol_map, api):
            raise ValueError('El padre recibido no coincide con el set local registrado')

    def check_destination(self, item) -> None:
        """Rechaza destinos que el universo actual ya no permite."""
        api = self.api
        if item['period'] not in self.timeframes or api.target_symbol_disabled(
            item['target_symbol'], self.universe,
            symbol_map=self.symbol_map, disabled_symbols=self.disabled,
        ):
            raise ValueError('Destino bloqueado por el universo actual')
        mapped = api.apply_symbol_map(item['target_symbol'], self.symbol_map)
        if not any(api.normalize_set_symbol(s) == api.normalize_set_symbol(mapped) for s in self.universe):
            raise ValueError('Instrumento fuera del universo del broker')

    def check_frozen(self, values: dict) -> None:
        """Rechaza un set que cambia un parametro hoy congelado."""
        for key, value in self.frozen.items():
            forced = self.globals_.get(key, value)
            if forced and key in values and protocol.normalized(values[key]) != protocol.normalized(forced):
                raise ValueError('El set difiere de un parámetro congelado actual')


def _prepared_package(args, path: Path, data: dict, directory: Path):
    """Paquete inmutable del lote, con los .set leidos del inbox."""
    package = {k: data[k] for k in ('version', 'batch_id', 'broker', 'account_type')}
    package['candidates'] = []
    for item in data['candidates']:
        if not re.fullmatch('[a-f0-9]{64}', str(item.get('fingerprint', ''))):
            raise ValueError('Nombre de candidato inválido')
        package['candidates'].append({**item,
            'set_b64': base64.b64encode((directory / (item['fingerprint'] + '.set')).read_bytes()).decode(),
            'parent_b64': base64.b64encode((directory / (item['fingerprint'] + '.parent.set')).read_bytes()).decode()})
    return protocol.validate_package(package, args.broker, args.account_type)


def _prepared_context(args, memory, api) -> _PreparedContext:
    """Reune universo, mapas y parametros congelados de este nodo."""
    universe = api.broker_universe_symbols(args)
    # Membership keys include aliases and are uppercased. They cannot be used as
    # MT5 names: read the actual instruments with broker spelling. Every broker
    # needs this — MT5 exits without opening the tester on a miscased symbol.
    groups, _ = api.load_asset_universe(Path(args.assets), include_disabled=True)
    frozen, _ = api.load_mutation_overrides()
    return _PreparedContext(
        args=args,
        memory=memory,
        api=api,
        universe=universe,
        execution_universe={symbol for symbols in groups.values() for symbol in symbols},
        disabled=api.load_disabled_symbols(
            api.disabled_symbols_file_for_account(args.account_type, args.broker)
        ),
        symbol_map=api.parse_symbol_map(args.symbol_map),
        timeframes=api.target_timeframe_universe(
            bool(args.experimental_long_timeframes), base_dir=api.BASE_DIR,
            broker=args.broker, account_type=args.account_type,
        ),
        frozen=frozen,
        globals_=api.load_global_params(),
    )


def _prepared_execution_set(ctx: _PreparedContext, execution_item, item, raw, strategy, values):
    """Copia de ejecucion con la grafia del broker y sus timeframes."""
    api = ctx.api
    lines = protocol.set_text(raw).splitlines()
    timeframe_keys = api.replace_timeframe_keys(lines, strategy, item['period'])
    # This verifies every strategy-specific timeframe before either kind
    # of prepared candidate can enter the evaluator.
    if protocol.set_params('\n'.join(lines).encode()) != values:
        raise ValueError('Los timeframes del set no coinciden con el destino')
    # Validate the immutable package first; only the execution copy gets the
    # broker spelling. MT5 reads ForceSymbol, not the candidate metadata.
    if values.get('ForceSymbol', '').split('||')[0] != execution_item['target_symbol']:
        if not api.replace_existing_current_value(lines, 'ForceSymbol', execution_item['target_symbol']):
            api.replace_or_add_plain_key(lines, 'ForceSymbol', execution_item['target_symbol'])
        raw = '\n'.join(lines).encode('utf-8')
    return raw, timeframe_keys


def _check_symbol_exploration(ctx: _PreparedContext, item, recovery: bool, key: str) -> bool:
    """Reglas propias de la exploracion de simbolo; True si ya esta validada."""
    # Exploration must reach new ground, so an instrument that already
    # produced a final positive is refused. A rebuild is the opposite
    # case on purpose: the destination is enabled and already proven,
    # but no set of its own still passes today's safety rules, so
    # without this the universe would keep it permanently closed.
    if item['mutation'].get('kind') != 'symbol_retarget':
        existing = ctx.memory.conn.execute('''select 1 from candidates c join candidate_final_tick_6m f
            on f.candidate_id=c.id where upper(c.target_symbol)=upper(?) and f.status='accepted' limit 1''',
            (item['target_symbol'],)).fetchone()
        if existing:
            raise ValueError('El símbolo de exploración ya tiene un positivo final')
    # A recovery keeps symbol and timeframe, so its single numeric step
    # falls through to the same rules any other mutation must satisfy.
    if recovery:
        return False
    if key != 'ForceSymbol':
        raise ValueError('La exploración de símbolo debe cambiar solo ForceSymbol')
    return True


def _check_numeric_mutation(ctx: _PreparedContext, item, parent, strategy, timeframe_keys) -> None:
    """Comprueba que el paso numerico cabe en las reglas vigentes."""
    from decimal import Decimal

    key = item['mutation']['key']
    choices = ctx.api.line_candidates(
        protocol.set_text(parent), strategy, {}, excluded_keys=timeframe_keys
    )
    if key not in choices:
        raise ValueError('Mutación no permitida por las reglas actuales del agente')
    _, parts, _ = choices[key]
    old, new, step = Decimal(parts[0]), Decimal(str(item['mutation']['new'])), Decimal(parts[2])
    if abs(new - old) != step or not Decimal(parts[1]) <= new <= Decimal(parts[3]):
        raise ValueError('Mutación fuera del paso/rango permitido')


def _validate_prepared_item(ctx: _PreparedContext, item, raw, parent):
    """Valida un candidato del lote y devuelve su copia de ejecucion."""
    recovery = item['mode'] == 'symbol_exploration' and item['mutation'].get('kind') == 'symbol_recovery'
    if _is_local_seed_parent(item):
        row = ctx.registered_seed(item)
        if not row:
            raise ValueError('La semilla padre no está activa y aceptada en esta memoria')
        ctx.verify_parent(row, parent)
    elif _is_local_lineage_parent(item):
        row = ctx.registered_lineage_candidate(item)
        if not row:
            raise ValueError('El padre de linaje no es un candidato local aceptado sin positivo final')
        ctx.verify_parent(row, parent)
    elif not _is_cross_broker_parent(item):
        row = ctx.registered_row(item, recovery)
        if not row:
            if recovery:
                raise ValueError('El padre de recuperación no es un intento previo de este nodo sin positivo final')
            raise ValueError('El padre no es un positivo final de esta memoria')
        ctx.verify_parent(row, parent)
    values = protocol.set_params(raw)
    strategy = values.get('Run_Strategy', '').split('||')[0]
    ctx.check_destination(item)
    execution_item = _broker_execution_item(
        item, ctx.execution_universe, ctx.symbol_map, ctx.api,
        strict=str(ctx.args.broker).strip().upper() == 'ICTRADING')
    ctx.check_frozen(values)
    raw, timeframe_keys = _prepared_execution_set(
        ctx, execution_item, item, raw, strategy, values
    )
    if item['mode'] == 'symbol_exploration' and _check_symbol_exploration(
        ctx, item, recovery, item['mutation']['key']
    ):
        return execution_item, raw, parent, strategy, timeframe_keys
    _check_numeric_mutation(ctx, item, parent, strategy, timeframe_keys)
    return execution_item, raw, parent, strategy, timeframe_keys


def load_prepared(args, memory, api):
    path = Path(args.prepared_manifest).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    directory = protocol.batch_dir(api.BASE_DIR, data['batch_id'])
    if path != directory / 'batch.json' or data['broker'] != args.broker or data['account_type'] != args.account_type:
        raise ValueError('Manifiesto fuera del inbox del broker/cuenta')
    decoded = _prepared_package(args, path, data, directory)
    ctx = _prepared_context(args, memory, api)
    validated = [_validate_prepared_item(ctx, item, raw, parent) for item, raw, parent in decoded]
    return data, directory, validated


def _prepared_run(args, memory, api, directory, batch_id, candidate_count):
    # The run's persisted provenance is also the recovery index if a process
    # exits after create_run but before publishing run.json.
    existing = memory.conn.execute("select id,output_dir from runs where json_extract(case when json_valid(config_json) then config_json else '{}' end,'$.prepared_batch_id')=?",
                                   (batch_id,)).fetchall()
    if len(existing)>1:
        raise ValueError('Hay varios runs para el mismo lote; no se relanza')
    if existing:
        run_id, output = existing[0]
        run_dir = Path(output)
    else:
        run_dir = api.resolve_workspace_path(args.output_dir)/('run_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_guided')
        protocol.assert_writable(run_dir,api.BASE_DIR)
        config = {'schema_version':2,'prepared_batch_id':batch_id,'broker':args.broker,'account_type':args.account_type,
                  'generation_mode':'discovery','args':api.json_safe(vars(args)),
                  'execution':{'from_date':args.from_date,'to_date':args.to_date},'prepared_no_remutation':True}
        run_id = memory.create_run(directory,run_dir,1,1,candidate_count,args.execute_backtests,args.dry_run,config=config)
    return run_id, run_dir


def run_prepared(args, memory, score_config, api):
    data,directory,validated = load_prepared(args,memory,api)
    batch_id = data['batch_id']
    run_id, run_dir = _prepared_run(args, memory, api, directory, batch_id, len(validated))
    checkpoint = protocol.read_run(api.BASE_DIR,batch_id) or {}
    if checkpoint.get('base_complete'):
        return 0
    candidate_ids, variants = {}, []
    for item,raw,parent,strategy,timeframe_keys in validated:
        target_dir = protocol.assert_writable(run_dir/'gen_001',api.BASE_DIR)
        target_dir.mkdir(parents=True,exist_ok=True)
        # run_tests derives its target from ForceSymbol and strategy timeframe;
        # explicit names also retain that context for existing report matching.
        name = api.safe_part(item['target_symbol'])+'_'+item['period']+'_'+item['fingerprint']
        target = target_dir/(name+'.set')
        if target.exists() and protocol.set_params(target.read_bytes())!=protocol.set_params(raw):
            raise ValueError('El set de ejecución cambió; no se sobrescribe')
        if not target.exists():
            target.write_bytes(raw)
        seed = Seed(directory/(item['fingerprint']+'.parent.set'),item['target_symbol'],item['period'],item['family'],strategy)
        change = item['mutation']
        pure_retarget = item['mode']=='symbol_exploration' and change.get('kind')!='symbol_recovery'
        if pure_retarget:
            # ForceSymbol is execution context, not a strategy parameter. Keep
            # the retarget provenance, but never expose it to mutation learning.
            detail = {'kind':change.get('kind','symbol_exploration'),'key':'ForceSymbol',
                      'old':change['old'],'new':change['new'],'wrapped':False}
        else:
            detail = {'key':change['key'],'old':float(change['old']),'new':float(change['new']),
                      'step':float(change['step']),'delta':float(change['new'])-float(change['old']),
                      'direction':int(change['direction']),'wrapped':False}
            if change.get('kind')=='symbol_recovery':
                detail = {**detail,'kind':'symbol_recovery','parent_stage':int(change['parent_stage'])}
        mutated_keys = () if pure_retarget else (change['key'],)
        variant = Variant(target,seed,item['target_symbol'],item['period'],mutated_keys,(),
                          'guided_prepared:'+item['mode'],tuple(timeframe_keys),(detail,))
        row = memory.conn.execute('select id from candidates where run_id=? and set_path=?',(run_id,str(target))).fetchone()
        if not row:
            memory.record_variant(run_id,1,variant)
            row = memory.conn.execute('select id from candidates where run_id=? and set_path=?',(run_id,str(target))).fetchone()
        candidate_ids[item['fingerprint']] = row[0]
        variants.append(variant)
    state = {'batch_id':batch_id,'run_id':run_id,'candidate_ids':candidate_ids,'base_complete':False}
    protocol.save_json(directory/'run.json',state)
    api.evaluate_generation(args,memory,run_dir,1,variants,score_config)
    state['base_complete'] = not args.dry_run and bool(args.execute_backtests)
    protocol.save_json(directory/'run.json',state)
    print(f'Prepared batch {batch_id}: run_id={run_id}; candidates={len(variants)}; no remutation')
    return 0
