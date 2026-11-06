#!/usr/bin/env python3
"""E2 real public validate differential; no oracle results enter Rust."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[1]
RUST = Path(os.environ.get('AW_E2_RUST_BINARY', ROOT / 'rust/target/debug/agent-workbench-rs'))
ROWS = 0


def snapshot(root):
    result = []
    for path in [root, *sorted(root.rglob('*'))]:
        st = path.lstat()
        content = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
        result.append((str(path.relative_to(root)), st.st_mode, st.st_ino, st.st_mtime_ns, st.st_ctime_ns, content))
    return result


def run(root, args):
    global ROWS
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    outputs = []
    for command in ([sys.executable, '-m', 'agent_workbench'], [str(RUST)]):
        before = snapshot(root)
        result = subprocess.run([*command, '--root', str(root), *args], cwd=ROOT,
                                env=env, capture_output=True, timeout=30)
        outputs.append((result.returncode, result.stdout, result.stderr))
        assert snapshot(root) == before, f'validate mutated fixture: {command} {args}'
    assert outputs[0] == outputs[1], (args, outputs)
    ROWS += 1


def main():
    from parity_oracle import require_python312
    require_python312()
    assert os.environ.get('TMPDIR'), 'explicit scratch TMPDIR required'
    with tempfile.TemporaryDirectory(prefix='validate-parity-') as temporary:
        root = Path(temporary)
        (root / 'catalog').mkdir()
        (root / 'profiles').mkdir()
        catalog = {'schemaVersion': 2, 'marketplace': {'id': 'sample', 'name': 'Sample',
                   'owner': {'name': 'Owner'}, 'description': 'Description'}, 'sources': [], 'plugins': []}
        profile = {'schemaVersion': 1, 'id': 'default', 'plugins': {'include': ['*'], 'exclude': []}, 'targets': []}
        (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
        (root / 'profiles/default.json').write_text(json.dumps(profile))
        run(root, ['validate', '--json', '--no-generated-check'])
        run(root, ['validate', '--no-generated-check'])
        profile['targets'] = [{'id': 'z-unknown'}, {'id': 'a-unknown'}, {'id': 'z-unknown'}]
        (root / 'profiles/default.json').write_text(json.dumps(profile))
        for flags in (['--json'], []):
            run(root, ['validate', '--no-generated-check', *flags])
        profile['targets'] = []
        (root / 'profiles/default.json').write_text(json.dumps(profile))
        (root / 'source/skills/one').mkdir(parents=True)
        (root / 'source/skills/two').mkdir()
        for name in ('one', 'two'):
            (root / f'source/skills/{name}/SKILL.md').write_text('---\nname: duplicate\ndescription: ok\n---\n')
        (root / 'source/native').mkdir()
        catalog['sources'] = [{'id': 'local', 'kind': 'local', 'path': 'source'}]
        catalog['plugins'] = [{'id': 'sample', 'source': 'local', 'artifacts': {
            'agent-skills': {'path': 'skills'}, 'mystery': {'path': 'absent'},
            'cursor-plugin': {'path': 'native'}, 'claude-plugin': {'path': 'native'}},
            'provenance': {'upstream': 'https://example.invalid/source', 'notice': 'NOTICE'}}]
        (root / 'catalog/plugins.json').write_text(json.dumps(catalog))
        for flags in (['--json'], []):
            run(root, ['validate', '--no-generated-check', *flags])
        run(root, ['validate', '--json'])
        profile['schemaVersion'] = None
        (root / 'profiles/default.json').write_text(json.dumps(profile))
        run(root, ['validate', '--json', '--no-generated-check'])
        profile['schemaVersion'] = 1
        profile['targets'] = [{'id':'unknown'}, {'id':'unknown'}]
        (root / 'profiles/default.json').write_text(json.dumps(profile))
        for name in ('one','two'):
            (root / f'source/skills/{name}/SKILL.md').write_text('---\nname: doublé🚀\ndescription: ok\n---\n')
        run(root, ['validate', '--json', '--no-generated-check'])
    malformed()
    wire_rows()
    json_field_rows()
    generated_rows()
    filesystem_rows()
    git_revision_rows()
    artifact_json_rows()
    wire_boundary_rows()
    upstream_rows()
    descriptive_nul_rows()
    print(f'validate parity: {ROWS} paired public CLI rows passed (bytes/exits/stderr/read-only identity)')


def malformed():
    catalog = {'schemaVersion': 2, 'marketplace': {'id': 'sample', 'name': 'Sample', 'owner': {'name': 'Owner'}, 'description': 'Description'}, 'sources': [{'id': 'local', 'kind': 'local', 'path': 'source'}], 'plugins': [{'id': 'sample', 'source': 'local', 'artifacts': {'agent-skills': {'path': 'skills'}}}]}
    profile = {'schemaVersion': 2, 'id': 'default', 'plugins': {'include': ['*'], 'exclude': []}, 'targets': [{'id': 'hermes'}]}
    rows = []
    def add(label, which, path, value, remove=False):
        docs = [deepcopy(catalog), deepcopy(profile)]
        node = docs[which]
        for key in path[:-1]:
            node = node[key]
        if remove:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        rows.append((label, docs))
    for which, paths in enumerate((
        [('schemaVersion',), ('marketplace',), ('marketplace','id'), ('marketplace','owner'), ('marketplace','owner','name'), ('sources',), ('sources',0,'id'), ('sources',0,'kind'), ('sources',0,'path'), ('plugins',), ('plugins',0,'id'), ('plugins',0,'source'), ('plugins',0,'artifacts'), ('plugins',0,'artifacts','agent-skills'), ('plugins',0,'artifacts','agent-skills','path')],
        [('schemaVersion',), ('id',), ('plugins',), ('plugins','include'), ('plugins','exclude'), ('targets',), ('targets',0,'id')])):
        for path in paths:
            for label, value, remove in [('null',None,False), ('omit',None,True), ('wrong',False,False)]:
                add(f'{which}:{path}:{label}', which, path, value, remove)
    for field in ['upstream', 'branch']:
        for value in [None, '', '-bad', 'bad value']:
            add(f'source:{field}:{value}', 0, ('sources',0,field), value)
    for value in [None, {}, {'upstream':None}, {'upstream':'https://example.invalid','notice':None}]:
        add(f'provenance:{value}', 0, ('plugins',0,'provenance'), value)
    for field in ['destination', 'components', 'projectRoot']:
        for value in [None, '', [], ['skills','skills'], ['unknown']]:
            add(f'target:{field}:{value}', 1, ('targets',0,field), value)
    for which, path in [(0, ('extra',)), (0, ('marketplace','extra')), (0, ('sources',0,'extra')), (0, ('plugins',0,'extra')), (1, ('extra',)), (1, ('targets',0,'extra'))]:
        add(f'unknown:{which}:{path}', which, path, 1)
    add('unsafe-source',0,('sources',0,'path'),'../outside')
    add('bad-artifact-key',0,('plugins',0,'artifacts'),{'BAD':{'path':'skills'}})
    add('source-duplicate',0,('sources',),catalog['sources']*2)
    add('plugin-duplicate',0,('plugins',),catalog['plugins']*2)
    add('unknown-include',1,('plugins','include'),['z','a'])
    add('unknown-exclude',1,('plugins','exclude'),['z','a'])
    add('missing-source',0,('plugins',0,'source'),'missing')
    add('missing-root',0,('sources',0,'path'),'absent')
    # Multi-defect precedence: shape before identity, full catalog before profile,
    # and full profile before duplicate catalog identity/resolution.
    docs = [deepcopy(catalog), deepcopy(profile)]
    docs[0]['plugins'][0].update(id='BAD', artifacts={})
    docs[1]['schemaVersion'] = None
    rows.append(('combined-artifacts-before-id-before-profile',docs))
    docs = [deepcopy(catalog), deepcopy(profile)]
    docs[0]['sources'] *= 2
    docs[1]['targets'][0]['components'] = None
    rows.append(('combined-profile-before-resolution',docs))
    failures = []
    with tempfile.TemporaryDirectory(prefix='validate-malformed-') as temporary:
        root = Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir()
        (root/'source/skills').mkdir(parents=True)
        for label, docs in rows:
            (root/'catalog/plugins.json').write_text(json.dumps(docs[0]))
            (root/'profiles/default.json').write_text(json.dumps(docs[1]))
            try:
                run(root,['validate','--json','--no-generated-check'])
            except AssertionError as error:
                failures.append((label,str(error)))
    assert not failures, '\n'.join(map(str,failures))
    print(f'validate malformed: {len(rows)} paired CLI rows passed')


def wire_rows():
    rows = ['{}', '[]', 'null', '', '{', '{"a":}', '{"a":1,}', '{"a" 1}', '{"a":1} trailing', '{"a":1,"a":2}', '{"a":1,"a":2,"z":{"child":1,"child":2}}', '{"z":{"x":1,"x":2}}', '\ufeff{}', '{"a":"unterminated}', '{"a":"bad\\q"}']
    rows += [
        '{"a":' + value + '}' for value in
        ['NaN','Infinity','-Infinity','1e999','-1e999','01','-01','1.','1e','1e+','1e-','+1','.1','--1','nan','Inf','-NaN','true false','[1,]','[','[1','"bad\\u12"','"bad\\uZZZZ"','"bad\\x20"','"raw\ncontrol"','"\\ud800"','"\\udfff"','"\\ud800\\udc00"','"\\ud800x"']
    ]
    failures = []
    with tempfile.TemporaryDirectory(prefix='validate-wire-') as temporary:
        root = Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir()
        for text in rows:
            (root/'catalog/plugins.json').write_text(text)
            try:
                run(root,['validate','--json','--no-generated-check'])
            except AssertionError as error:
                failures.append((text,str(error)))
    assert not failures, '\n'.join(map(str,failures))
    print(f'validate JSON wire: {len(rows)} paired CLI rows passed')


def json_field_rows():
    """Finite named wire edges through accepted, rejected, and rendered fields."""
    with tempfile.TemporaryDirectory(prefix='validate-json-fields-') as temporary:
        root = Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir()
        native = root/'source/native/.cursor-plugin'; native.mkdir(parents=True)
        catalog = {'schemaVersion':2,'marketplace':{'id':'sample','name':'Sample','owner':{'name':'Owner'},'description':'D'},'sources':[{'id':'local','kind':'local','path':'source'}],'plugins':[{'id':'sample','source':'local','artifacts':{'cursor-plugin':{'path':'native'}}}]}
        profile = {'schemaVersion':1,'id':'default','plugins':{'include':['*'],'exclude':[]},'targets':[]}
        manifest = {'name':'sample','description':'é 🚀 description'}
        cp=root/'catalog/plugins.json'; pp=root/'profiles/default.json'; mp=native/'plugin.json'
        def write(c=catalog,p=profile,m=manifest):
            for path,doc in [(cp,c),(pp,p),(mp,m)]: path.write_text(json.dumps(doc))
        write()
        values = [float('nan'),float('inf'),float('-inf'), '\ud800', '\udfff', '\ud800x\udfff', {'\ud800': ['\udfff', float('inf')]}, '\U00010000']
        failures=[]
        def check(label, flags=('--json', '--no-generated-check')):
            try: run(root,['validate',*flags])
            except AssertionError as error: failures.append((label,str(error)))
        for value in values:
            # Ignored schema annotations and unknown native fields are accepted
            # without narrowing Python's JSON parser to Unicode scalar strings.
            c=deepcopy(catalog); c['$schema']=value
            p=deepcopy(profile); p['$schema']=value
            m=dict(manifest, foreign=value)
            write(c,p,m); check(('ignored',repr(value))); check(('ignored-generated',repr(value)),('--json',))
            write(catalog,profile,dict(manifest,version=value))
            check(('version',repr(value))); check(('version-render',repr(value)),('--json',))
            if not isinstance(value,(str,dict)) or value == '\U00010000':
                subprocess.run([sys.executable,'-m','agent_workbench','--root',str(root),'render'],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),check=True,capture_output=True)
                check(('version-current',repr(value)),('--json',))
                (root/'catalog/lock.json').write_bytes(b'stale\n')
                check(('version-stale',repr(value)),('--json',))
            for field in ('name','description'):
                write(catalog,profile,dict(manifest,**{field:value}))
                check(('manifest',field,repr(value))); check(('manifest-render',field,repr(value)),('--json',))
            for field in ('id','name','description'):
                c=deepcopy(catalog); c['marketplace'][field]=value
                write(c,profile,manifest)
                check(('catalog',field,repr(value))); check(('catalog-render',field,repr(value)),('--json',))
        write()
        # Lexical overflow is accepted as infinity, not an arbitrary-precision float.
        mp.write_text('{"name":"sample","description":"ok","version":1e999}')
        check('overflow-version',('--json',))
        assert not failures, '\n'.join(map(str,failures))
    print('validate accepted/rejected JSON field and generated-byte rows passed')


def generated_rows():
    with tempfile.TemporaryDirectory(prefix='validate-generated-') as temporary:
        root = Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir()
        (root/'source/native').mkdir(parents=True)
        catalog = {'schemaVersion':2,'marketplace':{'id':'sample','name':'Márket 🚀','owner':{'name':'Owner'},'description':'D\né'},'sources':[{'id':'local','kind':'local','path':'source'}],'plugins':[{'id':'sample','source':'local','artifacts':{'cursor-plugin':{'path':'native'},'claude-plugin':{'path':'native'}}}]}
        profile = {'schemaVersion':1,'id':'default','plugins':{'include':['*'],'exclude':[]},'targets':[]}
        (root/'catalog/plugins.json').write_text(json.dumps(catalog))
        (root/'profiles/default.json').write_text(json.dumps(profile))
        (root/'source/native/plugin.json').write_text(json.dumps({'name':'sample','description':'description é 🚀'}))
        # Fallback manifests render successfully but validation still requires native names.
        run(root,['validate','--json'])
        for agent in ('cursor','claude'):
            path = root/f'source/native/.{agent}-plugin'
            path.mkdir()
            (path/'plugin.json').write_text(json.dumps({'name':'sample','description':'description é 🚀'}))
        run(root,['validate','--json'])
        # Canonical fixtures written by oracle; Rust independently computes expected bytes.
        subprocess.run([sys.executable,'-m','agent_workbench','--root',str(root),'render'],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),check=True,capture_output=True)
        run(root,['validate','--json'])
        run(root,['validate'])
        for version in [1e-7, -0.0, {'z':1e20,'a':[False,123456789012345678901234567890]}, [], 0, 'v-é']:
            path = root/'source/native/.cursor-plugin/plugin.json'
            path.write_text(json.dumps({'name':'sample','description':'description é 🚀','version':version}))
            subprocess.run([sys.executable,'-m','agent_workbench','--root',str(root),'render'],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),check=True,capture_output=True)
            run(root,['validate','--json'])
        subset = deepcopy(profile); subset.update(id='subset'); subset['plugins']['include']=[]
        (root/'profiles/subset.json').write_text(json.dumps(subset))
        run(root,['--profile','subset','validate','--json'])
        run(root,['--profile','subset','validate'])
        (root/'.cursor-plugin/marketplace.json').write_bytes(b'stale\n')
        run(root,['--profile','subset','validate','--json'])
        (root/'.claude-plugin/marketplace.json').unlink()
        (root/'.claude-plugin/marketplace.json').mkdir()
        run(root,['validate','--json'])
        (root/'.claude-plugin/marketplace.json').rmdir()
        (root/'.claude-plugin/marketplace.json').symlink_to(root/'source/native/plugin.json')
        run(root,['validate','--json'])
        for text in ['[]','{}','{"name":"wrong","description":"ok"}','{"name":"sample","description":null}', '{"name":"sample","name":"other"}', '{']:
            (root/'source/native/.cursor-plugin/plugin.json').write_text(text)
            run(root,['validate','--json'])
        (root/'profiles/default.json').write_text('{"schemaVersion":null}')
        run(root,['--profile','subset','validate','--json'])
        run(root,['--profile','subset','validate','--json','--no-generated-check'])
    print('validate canonical generated/native renderer rows passed')


def filesystem_rows():
    with tempfile.TemporaryDirectory(prefix='validate-paths-') as temporary:
        root = Path(temporary)/'repository'; root.mkdir()
        (root/'catalog').mkdir(); (root/'profiles').mkdir()
        catalog = {'schemaVersion':2,'marketplace':{'id':'sample','name':'Sample','owner':{'name':'Owner'},'description':'D'},'sources':[],'plugins':[]}
        profile = {'schemaVersion':1,'id':'default','plugins':{'include':['*'],'exclude':[]},'targets':[]}
        cp = root/'catalog/plugins.json'; pp = root/'profiles/default.json'
        run(root,['validate','--json'])
        cp.write_text(json.dumps(catalog))
        run(root,['validate','--json'])
        pp.write_text(json.dumps(profile))
        for data in [b'\xff', b'\xc3(', b'\xe2\x82', '{}'.encode('utf-16')]:
            cp.write_bytes(data)
            run(root,['validate','--json'])
        cp.write_text(json.dumps(catalog))
        for name in ['../escape','BAD','other']:
            run(root,['--profile',name,'validate','--json'])
        pp.write_text(json.dumps(dict(profile,id='wrong')))
        run(root,['validate','--json'])
        pp.unlink(); pp.symlink_to(root/'catalog/plugins.json')
        run(root,['validate','--json'])
        pp.unlink(); pp.write_text(json.dumps(profile))
        native = root/'source/native'; native.mkdir(parents=True)
        catalog['sources']=[{'id':'local','kind':'local','path':'source'}]
        catalog['plugins']=[{'id':'sample','source':'local','artifacts':{'cursor-plugin':{'path':'native'}}}]
        cp.write_text(json.dumps(catalog))
        (native/'.cursor-plugin').mkdir()
        manifest = native/'.cursor-plugin/plugin.json'
        (native/'plugin.json').write_text('{"name":"sample","description":"ok"}')
        manifest.symlink_to(native/'plugin.json')
        run(root,['validate','--json'])
        manifest.unlink(); manifest.write_text('{"name":"sample","description":"ok"}')
        run(root,['validate','--no-generated-check'])
        # A valid selection does not suppress inspection of unselected bad plugins.
        catalog['plugins'].append({'id':'unselected','source':'missing','artifacts':{'agent-skills':{'path':'missing'}},'provenance':{'upstream':'https://example.invalid','notice':'NOTICE'}})
        profile['plugins']['include']=['sample']
        pp.write_text(json.dumps(profile)); cp.write_text(json.dumps(catalog))
        run(root,['validate','--json','--no-generated-check'])
    print('validate filesystem/workspace rows passed')


def git_revision_rows():
    """VAL-GIT-1: preserve actual Git bytes; omit only the uncaught traceback."""
    global ROWS
    with tempfile.TemporaryDirectory(prefix='validate-git-') as temporary:
        root = Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir()
        catalog = {'schemaVersion':2,'marketplace':{'id':'sample','name':'Sample','owner':{'name':'Owner'},'description':'D'},'sources':[{'id':'submodule','kind':'git-submodule','path':'not-a-repository','upstream':'https://example.invalid/repo','branch':'main'}],'plugins':[]}
        profile = {'schemaVersion':1,'id':'default','plugins':{'include':[],'exclude':[]},'targets':[]}
        (root/'catalog/plugins.json').write_text(json.dumps(catalog))
        (root/'profiles/default.json').write_text(json.dumps(profile))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', GIT_CEILING_DIRECTORIES=str(root.parent))
        # Nonrepository and initialized repository without HEAD, human and JSON,
        # plus a prior issue that must not leak a partial report on uncaught error.
        (root/'not-a-repository').mkdir()
        for initialized in (False, True):
            if initialized:
                subprocess.run(['git','init','--quiet',str(root/'not-a-repository')],env=env,check=True)
            for targets in ([], [{'id':'unknown'}]):
                profile['targets'] = targets
                (root/'profiles/default.json').write_text(json.dumps(profile))
                for flags in ([], ['--json']):
                    before = snapshot(root)
                    git = subprocess.run(['git','-C',str(root/'not-a-repository'),'rev-parse','HEAD'],env=env,capture_output=True)
                    assert git.returncode != 0 and git.stderr
                    py = subprocess.run([sys.executable,'-m','agent_workbench','--root',str(root),'validate',*flags],cwd=ROOT,env=env,capture_output=True)
                    rs = subprocess.run([str(RUST),'--root',str(root),'validate',*flags],cwd=ROOT,env=env,capture_output=True)
                    assert py.returncode == 1 and py.stdout == b''
                    assert py.stderr.startswith(git.stderr + b'Traceback (most recent call last):\n') and b'CalledProcessError' in py.stderr
                    assert (rs.returncode,rs.stdout,rs.stderr) == (1,b'',git.stderr), (rs.returncode,rs.stdout,rs.stderr,git.stderr)
                    assert snapshot(root) == before
                    ROWS += 1
        run(root,['validate','--json','--no-generated-check'])
    print('validate Git failure: 8 authorized exception rows plus generated-check bypass passed')


def artifact_json_rows():
    with tempfile.TemporaryDirectory(prefix='validate-artifact-json-') as temporary:
        root=Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir(); (root/'source/hooks').mkdir(parents=True)
        catalog={'schemaVersion':2,'marketplace':{'id':'sample','name':'Sample','owner':{'name':'Owner'},'description':'D'},'sources':[{'id':'local','kind':'local','path':'source'}],'plugins':[{'id':'sample','source':'local','artifacts':{'hooks':{'path':'hooks'},'mcp':{'path':'mcp.json'}}}]}
        profile={'schemaVersion':1,'id':'default','plugins':{'include':['*'],'exclude':[]},'targets':[]}
        (root/'catalog/plugins.json').write_text(json.dumps(catalog)); (root/'profiles/default.json').write_text(json.dumps(profile))
        failures=[]
        for value in [float('nan'),float('inf'),float('-inf'),'\ud800','\udfff',{'\ud800':['\udfff',float('inf')]}]:
            for location in ('ignored','command','event','server','version'):
                hooks={'version':1,'hooks':{'beforeShellExecution':[{'command':'opaque'}]}}
                mcp={'mcpServers':{'sample':{'command':'opaque'}}}
                if location=='ignored': hooks['foreign']=value; mcp['foreign']=value
                elif location=='command': hooks['hooks']['beforeShellExecution'][0]['command']=value; mcp['mcpServers']['sample']['command']=value
                elif location=='event': hooks['hooks']={str(value):[{'command':'opaque'}]}
                elif location=='server': mcp['mcpServers']={str(value):{'command':'opaque'}}
                else: hooks['version']=value
                (root/'source/hooks/hooks.json').write_text(json.dumps(hooks)); (root/'source/mcp.json').write_text(json.dumps(mcp))
                for flags in (['--json','--no-generated-check'],['--json']):
                    try: run(root,['validate',*flags])
                    except AssertionError as error: failures.append((location,repr(value),str(error)))
        assert not failures, '\n'.join(map(str,failures))
    print('validate hook/MCP lossless JSON rows passed')


def wire_boundary_rows():
    """The same named lexemes reach every distinct E2 JSON reader/wrapper."""
    lexemes = ['NaN','Infinity','-Infinity','1e999','-1e999','1e-999','01','-01','1.','1e','1e+','1e-','+1','.1','--1','nan','Inf','-NaN','true false','[1,]','[','[1','"bad\\u12"','"bad\\uZZZZ"','"bad\\x20"','"raw\ncontrol"','"\\ud800"','"\\udfff"','"\\ud800\\udc00"','"\\ud800x"','"\\ud800\\uZZZZ"','"é 🚀\\q"']
    with tempfile.TemporaryDirectory(prefix='validate-wire-boundaries-') as temporary:
        root=Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir(); (root/'source/native/.cursor-plugin').mkdir(parents=True); (root/'source/hooks').mkdir()
        catalog={'schemaVersion':2,'marketplace':{'id':'sample','name':'Sample','owner':{'name':'Owner'},'description':'D'},'sources':[{'id':'local','kind':'local','path':'source'}],'plugins':[{'id':'sample','source':'local','artifacts':{'cursor-plugin':{'path':'native'},'hooks':{'path':'hooks'},'mcp':{'path':'mcp.json'}}}]}
        profile={'schemaVersion':1,'id':'default','plugins':{'include':['*'],'exclude':[]},'targets':[]}
        docs=[('catalog/plugins.json',catalog),('profiles/default.json',profile),('source/native/.cursor-plugin/plugin.json',{'name':'sample','description':'D'}),('source/hooks/hooks.json',{'version':1,'hooks':{}}),('source/mcp.json',{'mcpServers':{}})]
        failures=[]
        for filename,_ in docs:
            for lexeme in lexemes:
                for path,doc in docs: (root/path).write_text(json.dumps(doc))
                (root/filename).write_text('{"a":'+lexeme+'}')
                try: run(root,['validate','--json','--no-generated-check'])
                except AssertionError as error: failures.append((filename,lexeme,str(error)))
        assert not failures, '\n'.join(map(str,failures))
    print(f'validate JSON reader boundaries: {len(docs)*len(lexemes)} paired rows passed')


def upstream_rows():
    """Python 3.12 urlparse validation is separate from stored raw upstreams."""
    values = [
        'HTTPS://example.invalid/repo', ' https://example.invalid/repo',
        '\x01\x1f https://example.invalid/repo', 'ht\ttps://example.invalid/repo',
        'ht\rtps://example.invalid/repo', 'ht\ntps://example.invalid/repo',
        'SSH:repo', 'GIT://host/repo', 'https:', 'https://',
        'https://[invalid', 'https://invalid]', 'https://[invalid]',
        'https://[127.0.0.1]', 'https://[::1]', 'https://[fe80::1%zone]',
        'https://[::1%]', 'https://[::1%a%b]', 'https://[v1.host]',
        'https://[v.host]', 'https://[V1.host]', 'https://prefix[::1]',
        'https://[::1]suffix', 'https://user@[::1]:nonnumeric',
        'https://[x]@host', 'https://host\uff0fpath', 'https://host\u2100path',
        '//[invalid', 'ftp://[invalid', '-https://[invalid',
        'user@host:path', 'user@host:path\n', 'user@host:pa\nth',
        'user@host:path\r', 'user@host:path\t', 'user@host: ',
        'u@v@host:path', 'u@host:pa@th', 'u@host:', 'u@host:p\u2028ath',
        'u\x1c@host:path', 'u@ho\u00a0st:path', 'bad',
    ]
    with tempfile.TemporaryDirectory(prefix='validate-upstream-') as temporary:
        root = Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir()
        (root/'source/skills').mkdir(parents=True)
        catalog = {'schemaVersion':2,'marketplace':{'id':'sample','name':'Sample','owner':{'name':'Owner'},'description':'D'},'sources':[{'id':'local','kind':'local','path':'source'}],'plugins':[{'id':'sample','source':'local','artifacts':{'agent-skills':{'path':'skills'}}}]}
        profile = {'schemaVersion':1,'id':'default','plugins':{'include':['*'],'exclude':[]},'targets':[]}
        (root/'profiles/default.json').write_text(json.dumps(profile))
        failures = []
        for channel in ('source', 'provenance'):
            for value in values:
                c = deepcopy(catalog)
                if channel == 'source': c['sources'][0]['upstream'] = value
                else: c['plugins'][0]['provenance'] = {'upstream':value}
                (root/'catalog/plugins.json').write_text(json.dumps(c))
                for flags in (['--json','--no-generated-check'], []):
                    try: run(root,['validate',*flags])
                    except AssertionError as error: failures.append((channel,repr(value),str(error)))
        # Decode precedence: source URL before branch/profile; provenance URL
        # before notice/plugin identity. Keep each original diagnostic path.
        for channel in ('source', 'provenance'):
            c = deepcopy(catalog)
            if channel == 'source':
                c['sources'][0].update(upstream='https://[invalid', branch='-bad')
                c['plugins'][0]['provenance'] = {'upstream':'bad','notice':'../outside'}
            else:
                c['plugins'][0].update(id='BAD', provenance={'upstream':'bad','notice':'../outside'})
            (root/'catalog/plugins.json').write_text(json.dumps(c))
            try: run(root,['validate','--json','--no-generated-check'])
            except AssertionError as error: failures.append((channel,'combined',str(error)))
        # Oracle-written current files expose accidental normalization of raw
        # source/provenance strings in Rust's independent read-only renderer.
        for value in values[:8]:
            c = deepcopy(catalog)
            c['sources'][0]['upstream'] = value
            c['plugins'][0]['provenance'] = {'upstream':value}
            (root/'catalog/plugins.json').write_text(json.dumps(c))
            subprocess.run([sys.executable,'-m','agent_workbench','--root',str(root),'render'],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),check=True,capture_output=True)
            try: run(root,['validate','--json'])
            except AssertionError as error: failures.append(('raw-current',repr(value),str(error)))
        assert not failures, '\n'.join(map(str,failures))
    print('validate upstream urlparse/SCP, both channels, precedence and raw preservation passed')


def descriptive_nul_rows():
    """NUL is rejected before scalar projection, including owner.name siblings."""
    with tempfile.TemporaryDirectory(prefix='validate-description-nul-') as temporary:
        root = Path(temporary)
        (root/'catalog').mkdir(); (root/'profiles').mkdir()
        catalog = {'schemaVersion':2,'marketplace':{'id':'sample','name':'Sample','owner':{'name':'Owner'},'description':'D'},'sources':[],'plugins':[]}
        profile = {'schemaVersion':1,'id':'default','plugins':{'include':['*'],'exclude':[]},'targets':[]}
        (root/'profiles/default.json').write_text(json.dumps(profile))
        failures = []
        for path in [('name',), ('description',), ('owner','name')]:
            for value in ['\ud800\x00', '\x00\udfff', 'a\ud800\x00z', '\x00', '\ud800', '\udfff', 'a\ud800z']:
                c = deepcopy(catalog)
                node = c['marketplace']
                for key in path[:-1]: node = node[key]
                node[path[-1]] = value
                for combined in (False, True):
                    if combined: c['sources'] = [{'id':'BAD','kind':'local','path':'../outside','upstream':'https://[invalid'}]
                    (root/'catalog/plugins.json').write_text(json.dumps(c))
                    for flags in (['--json','--no-generated-check'], ['--json']):
                        try: run(root,['validate',*flags])
                        except AssertionError as error: failures.append((path,repr(value),combined,str(error)))
        assert not failures, '\n'.join(map(str,failures))
    print('validate descriptive surrogate/NUL, sibling owner and precedence passed')


if __name__ == '__main__':
    main()
