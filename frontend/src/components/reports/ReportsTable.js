import React from 'react';
import {
    Table,
    Thead,
    Tbody,
    Tr,
    Th,
    Td,
    Badge,
    Menu,
    MenuButton,
    MenuList,
    MenuItem,
    IconButton

} from '@chakra-ui/react';
import {
    ViewIcon,
    DownloadIcon,
    DeleteIcon,
    EmailIcon
} from '@chakra-ui/icons';
import { useLanguage } from '../../context/LanguageContext';

const ThreeDotsIcon = (props) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
        <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z" />
    </svg>
);

const ReportsTable = ({ reports, onViewRaw, onViewTemplate, onDownload, onDelete }) => {
    const { t } = useLanguage();

    const getStatusBadge = (report) => {
        const stats = report.summary_stats;
        const details = report.analysis_details_markdown || "";

        const isWorkerFailed = details.includes("Worker Failed") || details.includes("Fatal Gemini Error");
        const isStatsEmpty = !stats || Object.keys(stats).length === 0 || Object.values(stats).includes('N/A');

        const isError = isWorkerFailed || isStatsEmpty;

        return (
            <Badge colorScheme={isError ? 'red' : 'green'} variant="subtle" fontWeight="normal" px={2} py={1} borderRadius="full">
                {isError ? t('reportError') : t('reportSuccess')}
            </Badge>
        );
    };

    return (
        <Table variant="simple">
            <Thead>
                <Tr>
                    <Th>{t('hostname')}</Th>
                    <Th>{t('type')}</Th>
                    <Th>{t('time')}</Th>
                    <Th>{t('status')}</Th>
                    <Th textAlign="right">{t('actions')}</Th>
                </Tr>
            </Thead>
            <Tbody>
                {reports.length > 0 ? (
                    reports.map((report) => (
                        <Tr key={report.path}>
                            <Td fontWeight="medium">{report.hostname}</Td>
                            <Td>
                                <Badge variant="outline" colorScheme="blue" fontSize="0.8em" fontWeight="normal">
                                    {report.type}
                                </Badge>
                            </Td>
                            <Td fontSize="sm" color="gray.500">
                                {new Date(report.generated_time).toLocaleString()}
                            </Td>
                            <Td>
                                {getStatusBadge(report)}
                            </Td>
                            <Td textAlign="right">
                                <Menu>
                                    <MenuButton
                                        as={IconButton}
                                        icon={<ThreeDotsIcon style={{ width: '20px', height: '20px' }} />}
                                        variant="ghost"
                                        size="sm"
                                        aria-label="Options"
                                    />
                                    <MenuList>
                                        <MenuItem icon={<ViewIcon />} onClick={() => onViewRaw(report.path)}>
                                            {t('viewJson')}
                                        </MenuItem>
                                        <MenuItem icon={<EmailIcon />} onClick={() => onViewTemplate(report.path)}>
                                            {t('viewEmail')}
                                        </MenuItem>
                                        <MenuItem icon={<DownloadIcon />} onClick={() => onDownload(report.path)}>
                                            {t('download')}
                                        </MenuItem>
                                        <MenuItem icon={<DeleteIcon />} color="red.500" onClick={() => onDelete(report.path)}>
                                            {t('delete')}
                                        </MenuItem>
                                    </MenuList>
                                </Menu>
                            </Td>
                        </Tr>
                    ))
                ) : (
                    <Tr>
                        <Td colSpan={5} textAlign="center" py={6} color="gray.500">
                            {t('noFilesFound')}
                        </Td>
                    </Tr>
                )}
            </Tbody>
        </Table>
    );
};

export default ReportsTable;