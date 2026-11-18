import math
import re
import time
import json
from datetime import date, datetime

from observability.tracer import record_span
from visualization.models import ChartSpec


def query_hint(question):
    """规划与SQL生成共享的分析要求，不新增数据库查询。"""
    if re.search(r'变化|走势|趋势|逐日|逐月|每天|每月|每周|按日|按月|按周|按年',question):
        grain = '月' if re.search(r'月度|逐月|每月|按月',question) else '周' if re.search(r'每周|按周',question) else '年' if re.search(r'每年|按年',question) else '日'
        return (f'这是时间趋势分析。用户未指定粒度时，短区间按日、跨多月按月、跨多年按年；明确指定时按{grain}。'
                '必须按时间桶聚合指标、返回可排序的时间列并升序排序，不能仅返回整个期间的合计。'
                '时间列使用DATE/TIMESTAMP或ISO格式YYYY-MM-DD、YYYY-MM，避免只返回无年份的月份名称。'
                '图表需要完整聚合结果，超过行数上限时合理调粗粒度并说明；遵守已有指标和时间口径。')
    if re.search(r'分布|直方',question):
        return '分布分析应在SQL中按明确区间分桶并返回区间及频数，避免用被系统截断的明细推断总体分布。'
    if re.search(r'占比|构成|份额',question):
        return '构成分析须查询完整分类聚合，确保共同分母，不用截断的Top N冒充总体。'
    return ''


def numeric(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def temporal(value):
    if isinstance(value,(date,datetime)):
        return value.isoformat()
    if isinstance(value,str) and re.fullmatch(r'\d{4}(-\d{2}(-\d{2})?)?([ T].*)?',value):
        try:
            return datetime.fromisoformat(value.replace('Z','+00:00')).isoformat() if len(value)>10 else date.fromisoformat(value+'-01' if len(value)==7 else value+'-01-01' if len(value)==4 else value).isoformat()
        except ValueError:
            return None
    return None


def text_only(question):
    """明确的展示偏好优先于按结果形状自动选图。"""
    return bool(re.search(
        r'(?:只|仅)(?:用|使用|要|需)?(?:文字|文本)|'
        r'(?:不要|不用|无需|不需要|不必|别)(?:再)?(?:生成|绘制|展示|显示|提供|画|出)?(?:任何)?(?:图表|图片|图形|图|指标卡片|指标卡|卡片)|'
        r'(?:不|别)画图', question))


def wants_card(question):
    return bool(re.search(r'指标卡片|指标卡|数据卡片|卡片', question))


def choose_chart(question, result, source_id):
    if text_only(question):
        return None,'用户要求文字回答，跳过可视化' 
    if not result or result.get('is_truncated'):
        return None,'结果为空或已截断，保留表格'
    columns,rows = result['columns'],result['rows']
    if not rows or len(rows)>100 or len(set(columns))!=len(columns) or any(len(row)!=len(columns) for row in rows):
        return None,'数据为空、过多或列标识不明确'
    if any(isinstance(value,(list,dict)) or isinstance(value,float) and not math.isfinite(value) for row in rows for value in row):
        return None,'嵌套结果使用表格展示'
    if len(json.dumps(result,ensure_ascii=False))>50000:
        return None,'图表数据过大，保留表格'
    numbers = [i for i,name in enumerate(columns) if not re.search(r'(^id$|_id$|编号|序号|编码)',name,re.I)
               and any(row[i] is not None for row in rows)
               and all(row[i] is None or numeric(row[i]) for row in rows)]
    dates = [i for i in range(len(columns)) if all(row[i] is not None and temporal(row[i]) for row in rows)]
    categories = [i for i in range(len(columns)) if i not in numbers and i not in dates]
    if not numbers:
        return None,'没有适合绘图的数值指标'
    title = question.strip()[:70]
    chart_type,x,series = None,None,None
    y = numbers[:3]
    if len(rows)==1 and len(columns)==len(numbers) and len(numbers)<=3:
        if len(numbers)==1 and not wants_card(question):
            return None,'单个数值默认使用文字回答'
        chart_type,y = 'card',numbers
    elif re.search(r'明细|列出.*记录|详情',question):
        return None,'明细查询保留表格'
    elif dates and len(rows)>1:
        x = dates[0]
        if categories:
            series = categories[0]
            if len({row[series] for row in rows})>6:
                return None,'系列过多，保留表格'
            y = numbers[:1]
        keys = [(temporal(row[x]),str(row[series]) if series is not None else '') for row in rows]
        if len(set(keys))!=len(keys):
            return None,'同一时间与系列包含重复数据，需要按目标粒度聚合'
        rows = sorted(rows,key=lambda row:temporal(row[x]))
        chart_type = 'stacked_bar' if series is not None and re.search(r'构成|堆叠',question) and not re.search(r'平均|均价|率|比例',question) else 'line'
    elif len(numbers)>=2 and re.search(r'关系|相关|散点',question) and len(rows)>1:
        chart_type,x,y = 'scatter',numbers[0],[numbers[1]]
    elif categories and len(rows)>1:
        x,y = categories[0],numbers[:1]
        if len({str(row[x]) for row in rows})!=len(rows) or len(rows)>20:
            return None,'分类重复或过多，需要更合适的聚合'
        values = [row[y[0]] for row in rows]
        if re.search(r'占比|份额|构成',question) and not re.search(r'平均|均价|转化率|退款率',question) and len(rows)<=6 and all(numeric(v) and v>=0 for v in values) and sum(values)>0:
            chart_type = 'pie'
        elif re.search(r'分布|直方',question):
            chart_type = 'histogram'
        else:
            chart_type = 'horizontal_bar' if re.search(r'排行|排名|最高|最低|前\s*\d|top',question,re.I) or max(len(str(row[x])) for row in rows)>8 else 'bar'
    if not chart_type:
        return None,'当前结果不适合自动绘图'
    # 多个指标的单位未知时拆开输出，避免共享纵轴造成误导。
    if chart_type not in ('card','scatter'):
        y = y[:1]
    spec = ChartSpec(source_id=source_id,type=chart_type,title=title,columns=columns,rows=rows,
                     x=x,y=y,series=series,subtitle='基于本次完整查询结果 · '+('时间点以查询返回为准，空值保留缺口、不补零' if chart_type=='line' else '与数据表使用相同口径'))
    return spec.model_dump(mode='json'),'已生成'


def build_visualizations(state):
    started = time.perf_counter()
    charts,notes = [],[]
    if state.get('route')!='data':
        return {'visualizations':charts,'visualization_notes':notes}
    original_question = state.get('user_question','')
    question = state.get('rewritten_question') or original_question
    # 改写或任务拆解可能丢失展示偏好，始终检查用户原话。
    if text_only(original_question) or text_only(question):
        return {'visualizations':charts,'visualization_notes':['用户要求文字回答，跳过可视化']}
    tasks = state.get('task_results') or []
    sources = [(task['task_id'],task['question'],task.get('query_result')) for task in tasks if task.get('status')=='approved'] if tasks else (
        [('main_result',question,state.get('query_result'))] if state.get('success') else [])
    for source_id,prompt,result in sources:
        if len(charts)>=2:
            break
        try:
            payload = result.model_dump(mode='json') if hasattr(result,'model_dump') else result
            intent_prompt = prompt + ('，使用指标卡片展示' if wants_card(original_question) else '')
            chart,reason = choose_chart(intent_prompt,payload,source_id)
            if chart:
                charts.append(chart)
                if chart['type'] in ('line','bar','horizontal_bar') and chart['series'] is None and len(charts)<2:
                    metrics = [i for i,name in enumerate(payload['columns']) if i!=chart['x']
                               and not re.search(r'(^id$|_id$|编号|序号|编码)',name,re.I)
                               and any(row[i] is not None for row in payload['rows'])
                               and all(row[i] is None or numeric(row[i]) for row in payload['rows'])]
                    if len(metrics)>1:
                        second = dict(chart,y=[metrics[1]],source_id=source_id+'_metric_2',
                                      title=(chart['title']+' · '+payload['columns'][metrics[1]])[:100])
                        charts.append(ChartSpec.model_validate(second).model_dump(mode='json'))
            else:
                notes.append(reason)
        except (ValueError,TypeError,KeyError,OverflowError):
            notes.append('图表数据校验失败，保留文字与表格')
    record_span('visualization','tool',(time.perf_counter()-started)*1000,
                {'charts':len(charts),'notes':notes})
    return {'visualizations':charts,'visualization_notes':notes}
