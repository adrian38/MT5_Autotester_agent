"""Enter prepared sets into normal Discovery evaluation, without generating mutations."""
from __future__ import annotations
import base64
import json
import re
from datetime import datetime
from pathlib import Path

from manager_node_runtime import guided_batches as protocol
from .models import Seed, Variant


def load_prepared(args, memory, api):
    path = Path(args.prepared_manifest).resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    directory = protocol.batch_dir(api.BASE_DIR,data['batch_id'])
    if path!=directory/'batch.json' or data['broker']!=args.broker or data['account_type']!=args.account_type:
        raise ValueError('Manifiesto fuera del inbox del broker/cuenta')
    package = {k:data[k] for k in ('version','batch_id','broker','account_type')}
    package['candidates'] = []
    for item in data['candidates']:
        if not re.fullmatch('[a-f0-9]{64}',str(item.get('fingerprint',''))):
            raise ValueError('Nombre de candidato inválido')
        package['candidates'].append({**item,
            'set_b64':base64.b64encode((directory/(item['fingerprint']+'.set')).read_bytes()).decode(),
            'parent_b64':base64.b64encode((directory/(item['fingerprint']+'.parent.set')).read_bytes()).decode()})
    decoded = protocol.validate_package(package,args.broker,args.account_type)
    universe = api.broker_universe_symbols(args)
    disabled = api.load_disabled_symbols(api.disabled_symbols_file_for_account(args.account_type,args.broker))
    symbol_map = api.parse_symbol_map(args.symbol_map)
    timeframes = api.target_timeframe_universe(bool(args.experimental_long_timeframes),base_dir=api.BASE_DIR,
                                             broker=args.broker,account_type=args.account_type)
    frozen, _ = api.load_mutation_overrides()
    globals_ = api.load_global_params()
    validated = []
    for item, raw, parent in decoded:
        row = memory.conn.execute('''select c.set_path from candidates c join candidate_final_tick_6m f
            on f.candidate_id=c.id where c.id=? and f.status='accepted' ''',(item['parent_candidate_id'],)).fetchone()
        if not row:
            raise ValueError('El padre no es un positivo final de esta memoria')
        source = Path(row[0]).resolve()
        if not source.is_relative_to(api.BASE_DIR.resolve()) or protocol.set_text(source.read_bytes())!=protocol.set_text(parent):
            raise ValueError('El padre recibido no coincide con el set local aceptado')
        values = protocol.set_params(raw)
        strategy = values.get('Run_Strategy','').split('||')[0]
        if item['period'] not in timeframes or api.target_symbol_disabled(item['target_symbol'],universe,
                                                        symbol_map=symbol_map,disabled_symbols=disabled):
            raise ValueError('Destino bloqueado por el universo actual')
        mapped = api.apply_symbol_map(item['target_symbol'],symbol_map)
        if not any(api.normalize_set_symbol(s)==api.normalize_set_symbol(mapped) for s in universe):
            raise ValueError('Instrumento fuera del universo del broker')
        for key,value in frozen.items():
            forced = globals_.get(key,value)
            if forced and key in values and protocol.normalized(values[key])!=protocol.normalized(forced):
                raise ValueError('El set difiere de un parámetro congelado actual')
        key = item['mutation']['key']
        lines = protocol.set_text(raw).splitlines()
        timeframe_keys = api.replace_timeframe_keys(lines,strategy,item['period'])
        # This verifies every strategy-specific timeframe before either kind
        # of prepared candidate can enter the evaluator.
        if protocol.set_params('\n'.join(lines).encode()) != values:
            raise ValueError('Los timeframes del set no coinciden con el destino')
        if item['mode']=='symbol_exploration':
            existing = memory.conn.execute('''select 1 from candidates c join candidate_final_tick_6m f
                on f.candidate_id=c.id where upper(c.target_symbol)=upper(?) and f.status='accepted' limit 1''',
                (item['target_symbol'],)).fetchone()
            if existing:
                raise ValueError('El símbolo de exploración ya tiene un positivo final')
            if key!='ForceSymbol':
                raise ValueError('La exploración de símbolo debe cambiar solo ForceSymbol')
            validated.append((item,raw,parent,strategy,timeframe_keys))
            continue
        choices = api.line_candidates(protocol.set_text(parent),strategy,{},excluded_keys=timeframe_keys)
        if key not in choices:
            raise ValueError('Mutación no permitida por las reglas actuales del agente')
        _, parts, _ = choices[key]
        from decimal import Decimal
        old,new,step = Decimal(parts[0]),Decimal(str(item['mutation']['new'])),Decimal(parts[2])
        if abs(new-old)!=step or not Decimal(parts[1])<=new<=Decimal(parts[3]):
            raise ValueError('Mutación fuera del paso/rango permitido')
        validated.append((item,raw,parent,strategy,timeframe_keys))
    return data,directory,validated


def run_prepared(args, memory, score_config, api):
    data,directory,validated = load_prepared(args,memory,api)
    batch_id = data['batch_id']
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
        run_id = memory.create_run(directory,run_dir,1,1,len(validated),args.execute_backtests,args.dry_run,config=config)
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
        if item['mode']=='symbol_exploration':
            detail = {'kind':'symbol_exploration','key':'ForceSymbol','old':change['old'],
                      'new':change['new'],'wrapped':False}
        else:
            detail = {'key':change['key'],'old':float(change['old']),'new':float(change['new']),
                      'step':float(change['step']),'delta':float(change['new'])-float(change['old']),
                      'direction':int(change['direction']),'wrapped':False}
        variant = Variant(target,seed,item['target_symbol'],item['period'],(change['key'],),(),
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
