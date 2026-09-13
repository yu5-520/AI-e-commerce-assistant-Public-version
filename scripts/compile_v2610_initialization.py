#!/usr/bin/env python3
"""Compile the registered three-report fixture; no model calls or data mutation."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def digest(value):
    return 'sha256:'+hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def resolve_parameters(profile, category, store_id):
    parameters={'minimumRelativeThreshold':profile['minimumRelativeThreshold'],**profile['parameterDefaults']}
    allowed=set(parameters)
    provenance={key:'profile' for key in allowed}
    for level,key in [('enterprise',profile['enterpriseId']),('category',category),('store',store_id)]:
        override=profile.get('parameterOverrides',{}).get(level,{}).get(key,{})
        if set(override)-allowed:raise ValueError('parameter_override_not_registered')
        parameters.update(override);provenance.update({k:level+':'+key for k in override})
    for key,value in parameters.items():
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('parameter_invalid:'+key)
        if key=='reviewWindowDays' and (type(value) is not int or not 1<=value<=365):raise ValueError('review_window_invalid')
        if key in ('maximumRelativeDeterioration','minimumDeltaRealizationRate') and value>1:raise ValueError('parameter_ratio_invalid:'+key)
    return {**parameters,'provenance':provenance,'status':profile['parameterStatus']}


def planning_preset(unit,profile):
    parameters=unit['parameters'];base=unit['baseline']['roi']['second']
    return {'schema':'planning.preset.v2610.v1','status':'proposal_requires_agent2_plan_freeze',
        'metric':'roi','unit':'ratio','baseline':base,
        'targetValue':base+abs(base)*parameters['targetRelativeImprovement'],
        'targetFormula':'baseline + abs(baseline) * targetRelativeImprovement',
        'reviewWindowDays':parameters['reviewWindowDays'],
        'riskBoundary':{'maximumRelativeDeterioration':parameters['maximumRelativeDeterioration']},
        'acceptanceCriteria':{'minimumDeltaRealizationRate':parameters['minimumDeltaRealizationRate']},
        'parameters':parameters,'parameterHash':digest(parameters),
        'evaluationContract':profile['evaluationContract'],
        'evaluationContractHash':digest(profile['evaluationContract']),
        'executionPermissionGranted':False}


def supplementary_baselines(reports,profile):
    result={}
    for sheet,spec in profile['supplementarySheets'].items():
        snapshots=[]
        for report in reports:
            headers=report['sheetEvidence'][sheet]['headers'];data=report['sheetData'][sheet]
            evidence=report['sheetEvidence'][sheet]
            if len(data)!=evidence['rowCount'] or digest([headers,*data])!=evidence['contentHash']:raise ValueError('sheet_evidence_mismatch')
            rows={}
            for raw in data:
                if len(raw)!=len(headers):raise ValueError('sheet_row_shape')
                row=dict(zip(headers,raw));key=tuple(row[k] for k in spec['identityColumns'])
                if key in rows:raise ValueError('duplicate_sheet_scope')
                rows[key]=row
            snapshots.append(rows)
        if any(set(x)!=set(snapshots[0]) for x in snapshots):raise ValueError('sheet_scope_mismatch')
        entries=[]
        for key in sorted(snapshots[0]):
            a,b,c=[x[key] for x in snapshots]
            category=next((row['category'] for row in reports[1]['rows'] if row['store_id']==b['店铺ID'] and row['product_id']==b.get('商品ID')), '')
            params=resolve_parameters(profile,category,b['店铺ID']);metrics={}
            for field,unit in spec['metrics'].items():
                x,y=a.get(field),b.get(field)
                valid=all(type(v) in (int,float) and math.isfinite(v) for v in (x,y))
                rel=(y-x)/abs(x) if valid and abs(x)>profile['epsilon'] else None
                metrics[field]={'first':x,'second':y,'absoluteDelta':y-x if valid else None,'relativeDelta':rel,
                    'relativeThreshold':max(params['minimumRelativeThreshold'],abs(rel)) if rel is not None else None,
                    'unit':unit,'missingReason':None if rel is not None else 'INPUT_MISSING_OR_BASELINE_NEAR_ZERO'}
            entries.append({'identity':dict(zip(spec['identityColumns'],key)),'metrics':metrics,
                'parameterHash':digest(params),'holdout':{'reportId':reports[2]['reportId'],'facts':{k:c[k] for k in spec['metrics']}}})
        result[sheet]={'identityColumns':spec['identityColumns'],'metrics':spec['metrics'],
            'sourceRefs':[{'reportId':r['reportId'],'sheet':sheet,'contentHash':r['sheetEvidence'][sheet]['contentHash']} for r in reports[:2]],
            'relativeDeltaFormula':'(second - first) / abs(first)','thresholdFormula':profile['thresholdFormula'],
            'thresholdStatus':profile['thresholdStatus'],'observationCount':2,'realSampleCount':0,'entries':entries}
    return result


def compile_bundle(scenario, profile):
    reports=scenario['reports']
    if len(reports)!=3 or profile['trainingReportCount']!=2 or profile['holdoutReportCount']!=1:
        raise ValueError('initialization_requires_two_train_one_holdout')
    if len({r['reportId'] for r in reports})!=3 or [r['period'] for r in reports]!=sorted({r['period'] for r in reports}):
        raise ValueError('report_identity_or_order')
    snapshots=[]
    for report in reports:
        by_scope={}
        for row in report['rows']:
            scope=(row['store_id'],row['product_id'])
            if scope in by_scope:raise ValueError('duplicate_operating_unit')
            if set(row)-set(profile['fields']):raise ValueError('unregistered_report_field')
            for key,value in row.items():
                if isinstance(value,float) and not math.isfinite(value):raise ValueError('nonfinite_fact')
            by_scope[scope]=row
        snapshots.append(by_scope)
    if any(set(rows)!=set(snapshots[0]) for rows in snapshots):raise ValueError('report_scope_mismatch')
    training=[{'reportId':r['reportId'],'period':r['period'],'contentHash':digest(r)} for r in reports[:2]]
    fields=sorted(set().union(*(set(row) for rows in snapshots[:2] for row in rows.values())))
    units=[];methods=[]
    for scope in sorted(snapshots[0]):
        first,second,third=[rows[scope] for rows in snapshots]
        numeric=sorted(k for k in fields if type(first.get(k)) in (int,float) and type(second.get(k)) in (int,float))
        parameters=resolve_parameters(profile, second['category'], scope[0])
        threshold=parameters['minimumRelativeThreshold']
        baselines={}
        for field in numeric:
            a,b=first[field],second[field]
            relative=(b-a)/abs(a) if abs(a)>profile['epsilon'] else None
            baselines[field]={'sourceType':'system_derived','first':a,'second':b,'absoluteDelta':b-a,
                'relativeDelta':relative,'relativeDeltaFormula':'(second - first) / abs(first)',
                'relativeThreshold':max(threshold,abs(relative)) if relative is not None else None,
                'thresholdFormula':profile['thresholdFormula'],'minimumRelativeThreshold':threshold,'unit':profile['fields'][field]['unit'],'thresholdStatus':profile['thresholdStatus'],
                'missingReason':'BASELINE_NEAR_ZERO' if relative is None else None,
                'sourceRefs':[f"{r['reportId']}:{scope[0]}:{scope[1]}:{field}" for r in reports[:2]],
                'observationCount':2,'realSampleCount':0}
        derived={}
        for name,num,den,unit in [('cpc','ad_spend','clicks','CNY/click'),('revenue_ad_spend_ratio','revenue','ad_spend','ratio'),('clicks_per_traffic','clicks','traffic','ratio')]:
            x,y=second.get(num),second.get(den)
            valid=type(x) in (int,float) and type(y) in (int,float) and abs(y)>profile['epsilon']
            derived[name]={'value':x/y if valid else None,'formula':f'{num} / {den}',
                'formulaInputs':{num:x,den:y},'unit':unit,'sourceReportId':reports[1]['reportId'],
                'missingReason':None if valid else 'INPUT_MISSING_OR_DENOMINATOR_NEAR_ZERO',
                'interpretation':'derived proxy; does not replace reported roi/ctr or establish ad attribution'}
        unit={'storeId':scope[0],'productId':scope[1],'category':second['category'],'platform':second['platform'],
            'parameters':parameters,'parameterHash':digest(parameters),'baseline':baselines,'derived':derived,'reportedRoi':second.get('roi'),
            'roiDefinitionMismatch':derived['revenue_ad_spend_ratio']['value'] is not None and not math.isclose(second['roi'],derived['revenue_ad_spend_ratio']['value'],rel_tol=1e-6),
            'holdout':{'reportId':reports[2]['reportId'],'period':reports[2]['period'],'facts':third,'role':'held_out_not_used_for_initialization'}}
        units.append(unit)
        for agent,instruction in sorted(profile['methods'].items()):
            material={'knowledgeType':'initialization_method','agent':agent,'category':second['category'],
                'platform':second['platform'],'instruction':instruction,'scope':{'storeId':scope[0],'productId':scope[1]},
                'baseline':{k:baselines[k] for k in ('roi','conversion_rate','ctr') if k in baselines},'derived':derived,'profileHash':digest(profile),'sourceRefs':training,
                'sourceType':'inferred_method_from_synthetic_fixture','sampleCount':0,'realSampleCount':0,
                'businessSuccessClaim':False,'executionPermissionGranted':False}
            if agent=='agent2':
                material['planningPreset']=planning_preset(unit,profile)
            methods.append({'methodHash':digest(material),'payload':material})
    material={'schema':'business.initialization.bundle.v2610.v1','version':profile['version'],
        'profile':profile,'profileHash':digest(profile),'trainingReports':training,
        'initializationHash':digest({'profile':profile,'trainingReports':training,'methods':methods}),
        'supplementaryBaselines':supplementary_baselines(reports,profile),
        'evaluationStandards':profile['evaluationContract'],
        'evaluationStandardsHash':digest(profile['evaluationContract']),
        'scenarioHash':digest(scenario),'fields':fields,'operatingUnits':units,'methods':methods,
        'holdoutUsedForTraining':False,'realSampleCount':0,'automaticEnable':False}
    return {**material,'bundleHash':digest(material)}



def era_source():
    """Read the same deterministic workbooks offered by the existing report service."""
    import io
    from openpyxl import load_workbook
    from src.services.competition_sample_report_service import build_competition_sample_xlsx
    mapping={'store_id':'店铺ID','product_id':'商品ID','sku_id':'SKU ID','store_name':'店铺名称',
        'product_name':'商品名称','category':'一级类目','platform':'平台','stock':'库存数量',
        'roi':'ROI','traffic':'访客数','clicks':'广告点击数','ctr':'点击率','conversion_rate':'支付转化率',
        'gross_margin':'毛利率','ad_spend':'广告消耗','sales_volume':'支付件数','revenue':'支付金额','refund_rate':'退款率'}
    reports=[]
    for period in (1,2,3):
        raw=build_competition_sample_xlsx(period)
        book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
        sheets={s.title:list(s.iter_rows(values_only=True)) for s in book}
        records=[dict(zip(sheets['商品经营明细'][0],row)) for row in sheets['商品经营明细'][1:]]
        rows=[{**{key:row[column] for key,column in mapping.items()},'sourceFields':row} for row in records]
        reports.append({'reportId':f'ERA-RPT-{period:03d}','period':records[0]['统计日期'],'rows':rows,
            'xlsxHash':'sha256:'+hashlib.sha256(raw).hexdigest(),
            'sheetData':{name:[list(row) for row in values[1:]] for name,values in sheets.items() if name!='商品经营明细'},
            'sheetEvidence':{name:{'headers':list(values[0]),'rowCount':len(values)-1,'contentHash':digest(values)} for name,values in sheets.items()}})
        book.close()
    return {'schema':'competition.three_report_scenario.v1','scenarioId':'ERA-THREE-REPORT-INITIALIZATION',
        'sourceMapping':mapping,'sourceKind':'existing_downloadable_synthetic_workbooks','reports':reports}

def main():
    p=argparse.ArgumentParser();p.add_argument('--profile',default='config/v2610_initialization_profile.json');p.add_argument('--output',default='config/v2610_initialization_bundle.json');p.add_argument('--check',action='store_true');p.add_argument('--refresh-era',action='store_true');args=p.parse_args()
    profile=json.loads(Path(args.profile).read_text())
    if args.refresh_era:Path(profile['sourcePath']).write_text(json.dumps(era_source(),ensure_ascii=False,separators=(',',':'))+'\n')
    if profile['evaluationContract']!=json.loads((ROOT/'config/v269b_evaluation_contract.json').read_text()):raise ValueError('evaluation_contract_snapshot_stale')
    scenario=json.loads(Path(profile['sourcePath']).read_text())
    if scenario.get('scenarioId')=='ERA-THREE-REPORT-INITIALIZATION' and scenario!=era_source():raise ValueError('era_source_snapshot_stale')
    bundle=compile_bundle(scenario,profile)
    path=Path(args.output)
    if args.check:
        if json.loads(path.read_text())!=bundle:raise ValueError('initialization_bundle_stale')
    else:path.write_text(json.dumps(bundle,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n')
    print(json.dumps({'verified':True,'bundleHash':bundle['bundleHash'],'initializationHash':bundle['initializationHash'],'methodCount':len(bundle['methods']),'unitCount':len(bundle['operatingUnits'])}))

if __name__=='__main__':main()
