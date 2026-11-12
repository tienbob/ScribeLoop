"""Versioned synthetic multilingual passage-retrieval check; no cloud calls or private data."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import time
from pathlib import Path

from scribeloop_core.llm.meeting_ask import AskScope, MeetingEvidence
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.embeddings import EmbeddingConfig, LocalEmbeddingProvider
from scribeloop_core.storage.search import MeetingSearch
from scribeloop_core.storage.sessions import SessionStore

CASES=[
('Architecture','Kiến trúc hỗ trợ phát triển thành AI Agent và tích hợp plugin.','How can the architecture be extended?','Kiến trúc có thể mở rộng như thế nào?'),
('Identity','Khách hàng doanh nghiệp đăng nhập bằng SAML single sign-on.','How do enterprise customers sign in?','Khách hàng doanh nghiệp đăng nhập bằng cách nào?'),
('Reliability','Sao lưu cơ sở dữ liệu mỗi đêm và khôi phục trong bốn giờ.','What is the database recovery plan?','Kế hoạch khôi phục dữ liệu là gì?'),
('Launch','Beta ra mắt ngày 15 tháng 12 sau khi kiểm tra bảo mật.','When will beta launch and what must happen first?','Khi nào ra mắt bản beta và điều kiện là gì?'),
('Ownership','Lan chịu trách nhiệm hoàn thiện tài liệu hướng dẫn vào thứ Sáu.','Who owns the documentation and when is it due?','Ai phụ trách tài liệu và hạn chót khi nào?'),
('Billing','Giá gói doanh nghiệp là bốn mươi đô la mỗi người mỗi tháng.','What does the enterprise subscription cost?','Gói doanh nghiệp có giá bao nhiêu?'),
('Retention','Bản ghi âm được giữ ba mươi ngày trừ khi người dùng lưu trữ lâu dài.','How long is recorded audio retained?','Âm thanh được lưu trong bao lâu?'),
('Privacy','Dữ liệu cuộc họp được mã hóa trên máy và không dùng để huấn luyện mô hình.','What privacy guarantees apply to meeting data?','Dữ liệu cuộc họp được bảo vệ như thế nào?'),
('Mobile','Ứng dụng iOS bị hoãn sang quý hai vì thiếu kỹ sư.','Why was the mobile release delayed?','Tại sao bản phát hành di động bị hoãn?'),
('Vendor','Nhóm chọn nhà cung cấp Atlas thay cho Beacon vì độ trễ thấp hơn.','Which vendor was chosen and why?','Nhóm chọn nhà cung cấp nào và tại sao?'),
('Migration','The database migration uses a shadow table, validation, and a reversible cutover.','How will the database migration be rolled out?','Quy trình chuyển đổi cơ sở dữ liệu sẽ diễn ra như thế nào?'),
('Capacity','The service must sustain two thousand concurrent calls with regional failover.','What load and resilience must the service support?','Dịch vụ cần chịu tải và dự phòng như thế nào?'),
('Hiring','Recruiting will prioritize a senior security engineer before another designer.','Which role should recruiting fill first?','Nhóm tuyển dụng ưu tiên vị trí nào?'),
('Support','Escalate critical incidents to the on-call engineer within fifteen minutes.','How quickly must urgent incidents be escalated?','Sự cố khẩn cấp cần được chuyển cấp trong bao lâu?'),
('Payment','Invoices are payable within thirty days and discounts require finance approval.','What are the payment and discount rules?','Quy định thanh toán và giảm giá là gì?'),
('Accessibility','Keyboard navigation and screen-reader labels are required before the desktop release.','What accessibility requirements block desktop release?','Yêu cầu trợ năng nào cần hoàn tất trước phát hành?'),
('Experiment','The onboarding experiment compares a guided checklist against a single welcome screen.','What variants are being compared for onboarding?','Thử nghiệm hướng dẫn ban đầu so sánh những phương án nào?'),
('Integration','The CRM integration starts with Salesforce; HubSpot is deferred until next quarter.','Which CRM integration comes first?','Tích hợp CRM nào được triển khai trước?'),
('Localization','Japanese translation is scheduled after Vietnamese and English quality reviews.','What is the translation rollout order?','Thứ tự triển khai bản dịch là gì?'),
('Cancellation','Customers can cancel at any time, with access retained through the paid period.','What happens to access after a subscription is canceled?','Sau khi hủy đăng ký khách hàng còn truy cập được không?')]


async def evaluate():
    store=SessionStore(migrate(connect(':memory:')))
    expected=[]
    def meeting(title,texts):
        sid=store.create_recording(title,{})['id']
        for index,text in enumerate(texts):
            store.append(sid,{'text':text,'start_ms':index*1000,'end_ms':index*1000+900,
                'is_final':True,'speaker_key':f'deepgram:{sid}:mic:1'},stream='mic')
        with store.conn:
            store.conn.execute("UPDATE sessions SET status='completed' WHERE id=?",(sid,))
        return sid
    for title,fact,en,vi in CASES:
        sid=meeting(title,['The meeting started with a routine agenda check.',fact,
                           'Next time the team will review progress.'])
        source=store.segments(sid)[1]['id']
        expected.extend([(en,title,source,'en'),(vi,title,source,'vi')])
    for index in range(10):
        meeting(f'Distractor {index}',[f'The office cafeteria changed its lunch menu on day {index}.',
                                      'Everyone discussed weather and travel.'])
    config=EmbeddingConfig('Local','','granite-embedding-97m-multilingual-r2',384)
    embedder=LocalEmbeddingProvider(config)
    search=MeetingSearch(store,embedding_config=config,embedder=embedder,include_titles=False)
    rows=[]
    try:
        for question,title,source,language in expected:
            started=time.monotonic()
            result=await search.query_async(question,limit=20,match_all=False)
            elapsed=round((time.monotonic()-started)*1000,2)
            packet=await MeetingEvidence(store,embedding_config=config,embedder=embedder).retrieve(
                question,AskScope.resolve(store,'all'))
            rows.append({'question':question,'language':language,'expected_meeting':title,
                'search_rank':next((i for i,row in enumerate(result['results'],1) if row['source_id']==source),None),
                'ask_rank':next((i for i,row in enumerate(packet['passages'],1) if row['source_id']==source),None),
                'search_ms':elapsed})
        metrics={}
        for prefix in ('search','ask'):
            for limit in (1,5,20):
                metrics[f'{prefix}_recall_at_{limit}']=sum(row[f'{prefix}_rank'] is not None and
                    row[f'{prefix}_rank']<=limit for row in rows)/len(rows)
        return {'corpus_version':1,'provider':'Local actual ONNX; not Gemini','model':config.model,
                'meetings':30,'segments':80,'questions':40,
                'first_query_ms':rows[0]['search_ms'],
                'warm_p50_ms':statistics.median(row['search_ms'] for row in rows[1:]),
                'metrics':metrics,'results':rows,
                'limitations':'Synthetic in-memory developer check; not independent grounding, encrypted production or cloud acceptance.'}
    finally:
        store.conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args()
    result=asyncio.run(evaluate())
    if args.output_dir:
        args.output_dir.mkdir(parents=True,exist_ok=True)
        (args.output_dir/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        with (args.output_dir/'results.csv').open('w',newline='') as output:
            writer=csv.DictWriter(output,fieldnames=list(result['results'][0]))
            writer.writeheader(); writer.writerows(result['results'])
    print(json.dumps({key:value for key,value in result.items() if key!='results'},ensure_ascii=False,indent=2))
    if any(result['metrics'][f'{kind}_recall_at_5']<.9 or
           result['metrics'][f'{kind}_recall_at_20']<.95 for kind in ('search','ask')):
        raise SystemExit('Synthetic passage-recall regression')


if __name__=='__main__':
    main()

