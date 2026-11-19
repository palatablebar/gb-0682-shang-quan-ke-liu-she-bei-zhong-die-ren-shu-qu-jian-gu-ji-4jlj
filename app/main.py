import importlib,os,hashlib,json
from flask import Flask,jsonify,request,redirect,Response
from app.config import MODULES
from app.errors import DomainError
from app.workflow import run,load
from app import store,daily_report
from app.web import payload
from app.modules.m7_archive.export_out import bundle
from app.business_day.service import recompute

def create_app():
    app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=2_000_000
    for entry in MODULES:app.register_blueprint(importlib.import_module('app.modules.'+entry[0]+'.handler').bp)
    @app.errorhandler(DomainError)
    def domain_error(error):return jsonify(code=error.code,message=error.message),error.status
    @app.errorhandler(404)
    def missing(error):return jsonify(code='NOT_FOUND',message='入口不存在'),404
    @app.get('/')
    def index():return redirect('/'+MODULES[0][0])
    @app.get('/healthz')
    def health():return jsonify(status='ok')
    @app.post('/api/runs')
    def submit():
        content,params=payload();state=run(content,params);return jsonify(store.save(state['run_id'],state))
    @app.get('/api/runs')
    def list_runs():return jsonify(store.history())
    @app.get('/api/runs/<run_id>')
    def get_run(run_id):return jsonify(store.get(run_id))
    @app.post('/api/runs/<run_id>/review')
    def review(run_id):
        data=request.get_json(silent=True) or {}
        if not isinstance(data.get('version'),int):raise DomainError('VERSION','请提交复核版本')
        return jsonify(store.review(run_id,data['version'],data.get('decision')))
    @app.get('/api/archive/<run_id>/export.csv')
    def download(run_id):
        state=store.get(run_id)
        if not state.get('reconcile',{}).get('balanced'):raise DomainError('UNBALANCED','对账未平')
        return Response(bundle(state),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=evidence.csv'})
    @app.get('/api/daily_reports')
    def list_daily():return jsonify(daily_report.list_published())
    @app.get('/api/daily_reports/<business_day>')
    def get_daily(business_day):return jsonify(daily_report.get_published(business_day))
    @app.post('/api/daily_reports/recompute')
    def recompute_daily():
        data=request.get_json(silent=True) or {}
        if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
        run_id=data.get('run_id')
        state=store.get(run_id) if run_id else _latest_state()
        parameters=data.get('parameters')
        if not isinstance(parameters,dict):raise DomainError('PARAMETERS','参数必须是对象')
        recompute_id=data.get('recompute_id') or hashlib.sha256(
            (state['run_id']+json.dumps(parameters,sort_keys=True,ensure_ascii=False)).encode()
        ).hexdigest()[:20]
        result=recompute(state.get('calculations',[]),state.get('business_day'),
                         parameters,recompute_id)
        return jsonify(result)
    with app.app_context():
        if not store.history():
            state=run(load('dirty.csv'),{});store.save(state['run_id'],state)
    return app

app=create_app()
def _latest_state():
    history=store.history()
    if not history:raise DomainError('NOT_FOUND','尚无运行批次',404)
    return store.get(history[0]['id'])
if __name__=='__main__':app.run(host='0.0.0.0',port=8000)
